from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select

from app import inspection
from app.authorization import INSPECTION_ACTIONS, authorize_business_write
from app.contracts import InspectionQueuePage
from app.dependencies import DB, Actor
from app.models import InspectionAction, InspectionTask, Operator, Station, now
from app.scopes import records as scoped_records

router = APIRouter(prefix="/api/v1/inspections", tags=["inspections"])


class TaskInput(BaseModel):
    station_id: int
    title: str = Field(min_length=1, max_length=150)
    description: str = Field(min_length=1, max_length=4000)
    severity: str
    due_at: datetime


class ActionInput(BaseModel):
    version: int = Field(ge=1)
    action: str
    comment: str = Field(min_length=1, max_length=4000)


@router.post("", status_code=201)
def create(body: TaskInput, request: Request, user: Actor, db: DB):
    authorize_business_write(db, user, request, "inspections.assign")
    task = inspection.create(db, user, **body.model_dump())
    db.commit()
    return inspection.output(task)


@router.get("", response_model=InspectionQueuePage)
def listing(
    user: Actor,
    db: DB,
    q: str = Query("", max_length=150),
    status: str = Query("", max_length=30),
    severity: str = Query("", max_length=30),
    overdue: bool | None = None,
    record_id: int | None = Query(None, ge=1, le=9223372036854775807),
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    if status not in {"", "assigned", "replied", "returned", "closed"} or severity not in {
        "",
        "normal",
        "major",
    }:
        raise HTTPException(422, "不支持的任务状态或隐患等级")
    at = now()
    stmt = scoped_records(
        select(InspectionTask, Station.name, Operator.name)
        .join(Station, Station.id == InspectionTask.station_id)
        .join(Operator, Operator.id == InspectionTask.operator_id),
        InspectionTask,
        user,
    )
    if user.role == "operator" and user.operator_id is not None:
        stmt = stmt.where(InspectionTask.operator_id == user.operator_id)
    elif user.role != "regulator":
        raise HTTPException(403, "无权查看巡检任务")
    if q.strip():
        stmt = stmt.where(
            or_(
                InspectionTask.title.icontains(q.strip(), autoescape=True),
                Station.name.icontains(q.strip(), autoescape=True),
                Operator.name.icontains(q.strip(), autoescape=True),
            )
        )
    if status:
        stmt = stmt.where(InspectionTask.status == status)
    if severity:
        stmt = stmt.where(InspectionTask.severity == severity)
    if record_id is not None:
        stmt = stmt.where(InspectionTask.id == record_id)
    is_overdue = and_(InspectionTask.status != "closed", InspectionTask.due_at < at)
    if overdue is not None:
        stmt = stmt.where(is_overdue if overdue else ~is_overdue)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(
        stmt.order_by(InspectionTask.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return {
        "items": [
            {
                **inspection.output(task),
                "station_name": station_name,
                "operator_name": operator_name,
                "overdue": task.status != "closed" and task.due_at < at,
            }
            for task, station_name, operator_name in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "query_at": at.isoformat(),
    }


@router.get("/{task_id}")
def detail(task_id: int, user: Actor, db: DB):
    task = inspection.access(db, user, task_id)
    return {
        **inspection.output(task),
        "history": [
            {
                "id": a.id,
                "action": a.action,
                "comment": a.comment,
                "created_at": a.created_at.isoformat(),
            }
            for a in db.scalars(
                select(InspectionAction)
                .where(InspectionAction.task_id == task_id)
                .order_by(InspectionAction.id)
            )
        ],
    }


@router.post("/{task_id}/actions")
def action(task_id: int, body: ActionInput, request: Request, user: Actor, db: DB):
    capability = INSPECTION_ACTIONS.get(body.action)
    if capability is None:
        raise HTTPException(422, "不支持的巡检动作")
    authorize_business_write(db, user, request, capability)
    task = inspection.act(db, user, task_id, body.version, body.action, body.comment)
    db.commit()
    return inspection.output(task)
