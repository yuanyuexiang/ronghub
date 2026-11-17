"""One scoped query for inspection feedback facts, list filters and complete-query CSV."""

from datetime import date, datetime, time, timedelta
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import aliased

from app import inspection
from app.hourly_energy import BEIJING
from app.models import InspectionAction, InspectionTask, Operator, Station
from app.scopes import records as scoped_records

ExecutionState = Literal["reported", "not_reported", "unknown"]
Reply = aliased(InspectionAction, name="last_inspection_reply")


class InspectionFilters(BaseModel):
    q: str
    status: str
    severity: str
    overdue: bool | None
    record_id: int | None
    execution_state: Literal["", "reported", "not_reported", "unknown"]
    created_from: date | None
    created_to: date | None


def filters(
    q: str = Query("", max_length=150),
    status: Literal["", "assigned", "replied", "returned", "closed"] = "",
    severity: Literal["", "normal", "major"] = "",
    overdue: bool | None = None,
    record_id: int | None = Query(None, ge=1, le=9223372036854775807),
    execution_state: Literal["", "reported", "not_reported", "unknown"] = "",
    created_from: date | None = None,
    created_to: date | None = None,
):
    if created_to == date.max or (
        created_from is not None and created_to is not None and created_from > created_to
    ):
        raise HTTPException(422, "创建日期范围不正确：开始日不晚于结束日，结束日须早于9999-12-31")
    return InspectionFilters(
        q=q,
        status=status,
        severity=severity,
        overdue=overdue,
        record_id=record_id,
        execution_state=execution_state,
        created_from=created_from,
        created_to=created_to,
    )


Filters = Annotated[InspectionFilters, Depends(filters)]


def statement(user, selected, at):
    latest = (
        select(InspectionAction.id)
        .where(InspectionAction.task_id == InspectionTask.id, InspectionAction.action == "reply")
        .order_by(InspectionAction.created_at.desc(), InspectionAction.id.desc())
        .limit(1)
        .correlate(InspectionTask)
        .scalar_subquery()
    )
    stmt = scoped_records(
        select(InspectionTask, Station.name, Operator.name, Reply)
        .join(Station, Station.id == InspectionTask.station_id)
        .join(Operator, Operator.id == InspectionTask.operator_id)
        .outerjoin(Reply, Reply.id == latest),
        InspectionTask,
        user,
    )
    if user.role == "operator" and user.operator_id is not None:
        stmt = stmt.where(InspectionTask.operator_id == user.operator_id)
    elif user.role != "regulator":
        raise HTTPException(403, "无权查看巡检任务")
    if selected.q.strip():
        stmt = stmt.where(
            or_(
                InspectionTask.title.icontains(selected.q.strip(), autoescape=True),
                Station.name.icontains(selected.q.strip(), autoescape=True),
                Operator.name.icontains(selected.q.strip(), autoescape=True),
            )
        )
    if selected.status:
        stmt = stmt.where(InspectionTask.status == selected.status)
    if selected.severity:
        stmt = stmt.where(InspectionTask.severity == selected.severity)
    if selected.record_id is not None:
        stmt = stmt.where(InspectionTask.id == selected.record_id)
    overdue = and_(
        InspectionTask.status != "closed",
        InspectionTask.due_at.is_not(None),
        InspectionTask.due_at < at,
    )
    if selected.overdue is not None:
        stmt = stmt.where(overdue if selected.overdue else ~overdue)
    if selected.created_from is not None:
        stmt = stmt.where(
            InspectionTask.created_at >= datetime.combine(selected.created_from, time.min, BEIJING)
        )
    if selected.created_to is not None:
        stmt = stmt.where(
            InspectionTask.created_at
            < datetime.combine(selected.created_to + timedelta(days=1), time.min, BEIJING)
        )
    if selected.execution_state == "reported":
        stmt = stmt.where(Reply.id.is_not(None))
    elif selected.execution_state == "not_reported":
        stmt = stmt.where(Reply.id.is_(None), InspectionTask.status == "assigned")
    elif selected.execution_state == "unknown":
        stmt = stmt.where(Reply.id.is_(None), InspectionTask.status != "assigned")
    return stmt


def execution(task, reply):
    if reply is not None:
        return "reported"
    return "not_reported" if task.status == "assigned" else "unknown"


def output(row, at):
    task, station, operator, reply = row
    return {
        **inspection.output(task),
        "station_name": station,
        "operator_name": operator,
        "overdue": task.status != "closed" and task.due_at is not None and task.due_at < at,
        "execution_state": execution(task, reply),
        "last_reply_id": reply.id if reply is not None else None,
        "last_reply_submitted_at": reply.created_at.isoformat() if reply is not None else None,
        "last_reported_performed_at": reply.performed_at.isoformat()
        if reply is not None and reply.performed_at is not None
        else None,
    }
