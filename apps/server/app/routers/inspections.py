import csv
import io
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import load_only

from app import inspection
from app import inspection_queries as queries
from app.authorization import INSPECTION_ACTIONS, authorize_business_write, require
from app.contracts import InspectionDetailResponse, InspectionQueuePage, InspectionTaskResponse
from app.dependencies import DB, Actor, SnapshotActor
from app.hourly_energy import BEIJING
from app.inspection_documents import ExecutionContact
from app.models import Audit, InspectionAction, InspectionTask, now
from app.reporting import SnapshotDB
from app.richtext import RichDocument
from app.routers.statistics import csv_cell

router = APIRouter(prefix="/api/v1/inspections", tags=["inspections"])


class TaskInput(BaseModel):
    station_id: int
    title: str = Field(min_length=1, max_length=150)
    description: str = Field(min_length=1, max_length=4000)
    severity: str
    due_at: datetime
    rich_document: RichDocument | None = None


class ActionInput(BaseModel):
    version: int = Field(ge=1)
    action: str
    comment: str = Field(min_length=1, max_length=4000)
    rich_document: RichDocument | None = None
    contact: ExecutionContact | None = None
    performed_at: datetime | None = None


@router.post("", status_code=201, response_model=InspectionTaskResponse)
def create(body: TaskInput, request: Request, user: Actor, db: DB, response: Response):
    authorize_business_write(db, user, request, "inspections.assign")
    task = inspection.create(
        db, user, **body.model_dump(exclude={"rich_document"}), rich_document=body.rich_document
    )
    result = InspectionTaskResponse.model_validate(inspection.output(task))
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("", response_model=InspectionQueuePage)
def listing(
    user: SnapshotActor,
    db: SnapshotDB,
    response: Response,
    selected: queries.Filters,
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    response.headers["Cache-Control"] = "no-store"
    at = now()
    stmt = queries.statement(user, selected, at)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(
        stmt.order_by(InspectionTask.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return {
        "items": [queries.output(row, at) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "query_at": at.isoformat(),
    }


EXPORT_LIMIT = 10000
CSV_RESPONSES = {200: {"content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}}}}


def beijing_clock(value):
    return value.astimezone(BEIJING).isoformat() if value is not None else ""


@router.get("/export", response_class=Response, responses=CSV_RESPONSES)
def export(user: SnapshotActor, db: SnapshotDB, selected: queries.Filters):
    require(db, user, "inspections.read")
    require(db, user, "statistics.export")
    at = now()
    stmt = queries.statement(user, selected, at).options(
        load_only(
            InspectionTask.id,
            InspectionTask.plan_id,
            InspectionTask.station_id,
            InspectionTask.operator_id,
            InspectionTask.title,
            InspectionTask.description,
            InspectionTask.severity,
            InspectionTask.status,
            InspectionTask.due_at,
            InspectionTask.created_at,
            InspectionTask.is_demo,
        ),
        load_only(
            queries.Reply.id,
            queries.Reply.created_at,
            queries.Reply.performed_at,
            queries.Reply.contact_name,
            queries.Reply.contact_phone,
            queries.Reply.comment,
        ),
    )
    rows = db.execute(stmt.order_by(InspectionTask.id.desc()).limit(EXPORT_LIMIT + 1)).all()
    if len(rows) > EXPORT_LIMIT:
        raise HTTPException(422, "单次最多导出10000条，请缩小筛选范围；未截断导出")
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "任务编号",
            "计划编号",
            "任务标题",
            "场站（当前名称）",
            "受派企业（当前名称）",
            "隐患等级",
            "办理状态",
            "反馈状态",
            "创建时间（北京时间）",
            "任务期限（北京时间）",
            "查询时是否逾期",
            "最近反馈记录编号",
            "最近反馈提交时间（北京时间）",
            "该次填报执行时间（北京时间）",
            "该次执行联系人",
            "该次联系方式",
            "该次反馈正文",
            "任务要求",
            "演示数据",
        ]
    )
    status_names = {
        "assigned": "待整改",
        "replied": "待复核",
        "returned": "退回补充",
        "closed": "已关闭",
    }
    executions = {"reported": "已反馈", "not_reported": "未反馈", "unknown": "历史未留存"}
    for task, station, operator, reply in rows:
        writer.writerow(
            [
                csv_cell(value)
                for value in [
                    task.id,
                    task.plan_id,
                    task.title,
                    station,
                    operator,
                    "重大" if task.severity == "major" else "一般",
                    status_names.get(task.status, task.status),
                    executions[queries.execution(task, reply)],
                    beijing_clock(task.created_at),
                    beijing_clock(task.due_at),
                    task.status != "closed" and task.due_at is not None and task.due_at < at,
                    reply.id if reply is not None else None,
                    beijing_clock(reply.created_at) if reply is not None else "",
                    beijing_clock(reply.performed_at) if reply is not None else "",
                    reply.contact_name if reply is not None else "",
                    reply.contact_phone if reply is not None else "",
                    reply.comment if reply is not None else "",
                    task.description,
                    task.is_demo,
                ]
            ]
        )
    result = Response(
        "\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="ronghub-inspections.csv"',
            "Cache-Control": "no-store",
        },
    )
    db.add(
        Audit(
            user_id=user.id,
            action="inspections.export",
            detail={
                "rows": len(rows),
                "execution_state": selected.execution_state,
                "created_from": selected.created_from.isoformat()
                if selected.created_from
                else None,
                "created_to": selected.created_to.isoformat() if selected.created_to else None,
            },
        )
    )
    db.commit()
    return result


@router.get("/{task_id}", response_model=InspectionDetailResponse)
def detail(task_id: int, user: SnapshotActor, db: SnapshotDB, response: Response):
    response.headers["Cache-Control"] = "no-store"
    task = inspection.access(db, user, task_id)
    return {
        **inspection.output(task),
        "history": [
            {
                "id": a.id,
                "action": a.action,
                "comment": a.comment,
                "rich_document": a.rich_document,
                "contact": {"name": a.contact_name, "phone": a.contact_phone}
                if a.contact_name is not None
                else None,
                "performed_at": a.performed_at.isoformat() if a.performed_at is not None else None,
                "created_at": a.created_at.isoformat(),
            }
            for a in db.scalars(
                select(InspectionAction)
                .where(InspectionAction.task_id == task_id)
                .order_by(InspectionAction.id)
            )
        ],
    }


@router.post("/{task_id}/actions", response_model=InspectionTaskResponse)
def action(
    task_id: int, body: ActionInput, request: Request, user: Actor, db: DB, response: Response
):
    capability = INSPECTION_ACTIONS.get(body.action)
    if capability is None:
        raise HTTPException(422, "不支持的巡检动作")
    authorize_business_write(db, user, request, capability)
    task = inspection.act(
        db,
        user,
        task_id,
        body.version,
        body.action,
        body.comment,
        body.rich_document,
        body.contact,
        body.performed_at,
    )
    result = InspectionTaskResponse.model_validate(inspection.output(task))
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result
