from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.authorization import APPLICATION_ACTIONS, authorize_business_write
from app.contracts import StationApplicationDetail, StationApplicationPage, StationApplicationRecord
from app.dependencies import DB, Actor
from app.models import Operator, Station, StationApplication
from app.scopes import records as scoped_records
from app.workflows import KINDS

router = APIRouter()


def named_output(db, application):
    from app.workflows import output

    return {
        **output(application),
        "station_name": db.scalar(select(Station.name).where(Station.id == application.station_id)),
        "operator_name": db.scalar(
            select(Operator.name).where(Operator.id == application.operator_id)
        ),
    }


class ApplicationInput(BaseModel):
    station_id: int
    kind: str
    payload: dict = Field(default_factory=dict)


class ActionInput(BaseModel):
    action: str
    version: int
    comment: str = Field(default="", max_length=2000)


@router.post("/api/v1/applications", status_code=201, response_model=StationApplicationRecord)
def create_application(body: ApplicationInput, user: Actor, db: DB, request: Request):
    authorize_business_write(db, user, request, "applications.create")
    from app.workflows import create

    result = create(db, user, body.station_id, body.kind, body.payload)
    db.commit()
    return named_output(db, result)


@router.get("/api/v1/applications", response_model=StationApplicationPage)
def applications(
    user: Actor,
    db: DB,
    q: str = Query("", max_length=150),
    status: str = Query("", max_length=30),
    kind: str = Query("", max_length=30),
    application_id: int | None = Query(None, ge=1, le=9223372036854775807),
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    from app.workflows import output

    if status not in {"", "draft", "submitted", "rejected", "approved", "withdrawn"}:
        raise HTTPException(422, "不支持的申请状态")
    if kind and kind not in KINDS:
        raise HTTPException(422, "不支持的申请类型")
    stmt = scoped_records(
        select(StationApplication, Station.name, Operator.name)
        .join(Station, Station.id == StationApplication.station_id)
        .join(Operator, Operator.id == StationApplication.operator_id),
        StationApplication,
        user,
    )
    if user.role == "operator" and user.operator_id is not None:
        stmt = stmt.where(StationApplication.operator_id == user.operator_id)
    elif user.role != "regulator":
        raise HTTPException(403, "无权查看场站申请")
    if q.strip():
        stmt = stmt.where(
            or_(
                Station.name.icontains(q.strip(), autoescape=True),
                Operator.name.icontains(q.strip(), autoescape=True),
            )
        )
    if status:
        stmt = stmt.where(StationApplication.status == status)
    if kind:
        stmt = stmt.where(StationApplication.kind == kind)
    if application_id is not None:
        stmt = stmt.where(StationApplication.id == application_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(
        stmt.order_by(StationApplication.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return {
        "items": [
            {**output(a), "station_name": station_name, "operator_name": operator_name}
            for a, station_name, operator_name in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/api/v1/applications/{application_id}", response_model=StationApplicationDetail)
def application_detail(application_id: int, user: Actor, db: DB):
    from app.models import ApprovalAction, StationApplication

    a = db.scalar(
        scoped_records(select(StationApplication), StationApplication, user).where(
            StationApplication.id == application_id
        )
    )
    if not a or (user.role == "operator" and a.operator_id != user.operator_id):
        raise HTTPException(404, "申请不存在或不可访问")
    if user.role not in {"operator", "regulator"}:
        raise HTTPException(403, "无权查看场站申请")
    return {
        **named_output(db, a),
        "history": [
            {"action": r.action, "comment": r.comment, "created_at": r.created_at.isoformat()}
            for r in db.scalars(
                select(ApprovalAction)
                .where(ApprovalAction.application_id == a.id)
                .order_by(ApprovalAction.id)
            )
        ],
    }


@router.post(
    "/api/v1/applications/{application_id}/actions", response_model=StationApplicationRecord
)
def application_action(
    application_id: int, body: ActionInput, user: Actor, db: DB, request: Request
):
    capability = APPLICATION_ACTIONS.get(body.action)
    if capability is None:
        raise HTTPException(422, "不支持的申请动作")
    authorize_business_write(db, user, request, capability)
    from app.workflows import act

    result = act(db, user, application_id, body.action, body.version, body.comment)
    db.commit()
    return named_output(db, result)


class DraftEditInput(BaseModel):
    version: int
    note: str = Field(default="", max_length=2000)
    name: str | None = Field(default=None, max_length=150)
    address: str | None = Field(default=None, max_length=255)


@router.patch("/api/v1/applications/{application_id}", response_model=StationApplicationRecord)
def edit_application(
    application_id: int, body: DraftEditInput, user: Actor, db: DB, request: Request
):
    authorize_business_write(db, user, request, "applications.edit")
    from app.business_audit import state, transition
    from app.models import ApprovalAction, StationApplication, now
    from app.workflows import allowed_demo_rules

    allowed_demo_rules()
    a = db.scalar(
        scoped_records(select(StationApplication), StationApplication, user)
        .where(StationApplication.id == application_id)
        .with_for_update()
    )
    if not a or (user.role == "operator" and a.operator_id != user.operator_id):
        raise HTTPException(404, "申请不存在或不可访问")
    if user.role != "operator":
        raise HTTPException(403, "仅所属运营商可编辑")
    if a.status not in {"draft", "rejected"} or a.version != body.version:
        raise HTTPException(409, "申请已更新或不处于可编辑状态，请刷新")
    before = state(a)
    a.payload = {"note": body.note}
    if a.kind == "change":
        if body.name is not None:
            a.payload["name"] = body.name.strip()
        if body.address is not None:
            a.payload["address"] = body.address.strip()
    a.version += 1
    a.updated_at = now()
    db.add(
        ApprovalAction(application_id=a.id, actor_id=user.id, action="edit", comment="更新申请信息")
    )
    transition(db, user, "applications.edit", a.id, before, state(a), "station_application")
    db.commit()
    return named_output(db, a)
