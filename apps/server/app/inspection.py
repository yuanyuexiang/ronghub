"""Example inspection transitions pending customer confirmation."""

from fastapi import HTTPException
from sqlalchemy import select, update

from app.business_audit import state, transition
from app.config import settings
from app.models import InspectionAction, InspectionAttachment, InspectionTask, Station, now
from app.scopes import records as scoped_records
from app.scopes import station_scope


def demo_only():
    if not settings.demo_mode:
        raise HTTPException(409, "巡检闭环规则待确认，写入仅限演示环境")


def access(db, user, task_id, lock=False):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "无权访问巡检任务")
    stmt = scoped_records(select(InspectionTask), InspectionTask, user).where(
        InspectionTask.id == task_id
    )
    if user.role == "operator":
        stmt = stmt.where(InspectionTask.operator_id == user.operator_id)
    task = db.scalar(stmt.with_for_update() if lock else stmt)
    if not task:
        raise HTTPException(404, "任务不存在或不可访问")
    return task


def create(db, user, station_id, title, description, severity, due_at):
    demo_only()
    if user.role != "regulator":
        raise HTTPException(403, "仅监管人员可下发巡检")
    station = db.scalar(station_scope(select(Station), user).where(Station.id == station_id))
    if not station:
        raise HTTPException(404, "场站不存在")
    if due_at.tzinfo is None or due_at <= now():
        raise HTTPException(422, "整改期限需带时区且晚于当前时间")
    if severity not in {"normal", "major"} or not title.strip() or not description.strip():
        raise HTTPException(422, "请填写任务标题、内容与隐患等级")
    task = InspectionTask(
        station_id=station.id,
        operator_id=station.operator_id,
        title=title.strip(),
        description=description.strip(),
        severity=severity,
        due_at=due_at,
        created_by=user.id,
    )
    db.add(task)
    db.flush()
    db.add(
        InspectionAction(task_id=task.id, actor_id=user.id, action="assign", comment=description)
    )
    transition(db, user, "inspections.assign", task.id, None, state(task), "inspection_task")
    return task


def act(db, user, task_id, version, action, comment):
    demo_only()
    task = access(db, user, task_id, True)
    before = state(task)
    if task.version != version:
        raise HTTPException(409, "任务已更新，请刷新")
    if not comment.strip():
        raise HTTPException(422, "请填写整改反馈或处理依据")
    if action == "reply":
        if user.role != "operator":
            raise HTTPException(403, "仅所属运营商可整改回复")
        if task.status not in {"assigned", "returned"}:
            raise HTTPException(409, "当前状态不可回复")
        task.status = "replied"
    elif action in {"return", "close"}:
        if user.role != "regulator":
            raise HTTPException(403, "仅监管人员可复核")
        if task.status != "replied":
            raise HTTPException(409, "需收到回复后复核")
        task.status = "returned" if action == "return" else "closed"
    else:
        raise HTTPException(422, "不支持的巡检动作")
    task.version += 1
    record = InspectionAction(
        task_id=task.id, actor_id=user.id, action=action, comment=comment.strip()
    )
    db.add(record)
    db.flush()
    if action == "reply":
        db.execute(
            update(InspectionAttachment)
            .where(
                InspectionAttachment.task_id == task.id, InspectionAttachment.action_id.is_(None)
            )
            .values(action_id=record.id)
        )
    transition(db, user, "inspections." + action, task.id, before, state(task), "inspection_task")
    return task


def output(task):
    return {
        "id": task.id,
        "station_id": task.station_id,
        "operator_id": task.operator_id,
        "title": task.title,
        "description": task.description,
        "severity": task.severity,
        "due_at": task.due_at.isoformat(),
        "status": task.status,
        "version": task.version,
        "overdue": task.status != "closed" and task.due_at < now(),
        "is_demo": task.is_demo,
        "created_at": task.created_at.isoformat(),
    }
