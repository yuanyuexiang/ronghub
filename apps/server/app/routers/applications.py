import csv
import io
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, or_, select

from app.application_evidence import ApplicationContact, normalize_payload
from app.authorization import APPLICATION_ACTIONS, authorize_business_write, require
from app.business_dates import apply_beijing_days, submission_time
from app.contracts import StationApplicationDetail, StationApplicationPage, StationApplicationRecord
from app.dependencies import DB, Actor, SnapshotActor
from app.models import Attachment, Audit, Operator, Station, StationApplication
from app.reporting import SnapshotDB
from app.routers.statistics import csv_cell, csv_response
from app.scopes import records as scoped_records
from app.workflows import KINDS, output

router = APIRouter()


last_submit = submission_time(StationApplication)
active_materials = (
    select(func.count())
    .where(Attachment.application_id == StationApplication.id, Attachment.active.is_(True))
    .correlate(StationApplication)
    .scalar_subquery()
)


def application_rows():
    return (
        select(
            StationApplication,
            Station.name,
            Station.address,
            Operator.name,
            last_submit.label("last_submitted_at"),
            active_materials.label("active_material_count"),
        )
        .join(Station, Station.id == StationApplication.station_id)
        .join(Operator, Operator.id == StationApplication.operator_id)
    )


def record_output(row):
    a, station_name, station_address, operator_name, submitted, materials = row
    return {
        **output(a),
        "station_name": station_name,
        "station_address": station_address,
        "operator_name": operator_name,
        "last_submitted_at": submitted.isoformat() if submitted else None,
        "active_material_count": materials,
    }


def named_output(db, application):
    return record_output(
        db.execute(application_rows().where(StationApplication.id == application.id)).one()
    )


class ApplicationInput(BaseModel):
    station_id: int
    kind: str
    payload: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def contact_payload(self):
        self.payload = normalize_payload(self.payload)
        return self


class ActionInput(BaseModel):
    action: str
    version: int
    comment: str = Field(default="", max_length=2000)
    review_token: str | None = Field(None, pattern="^[a-f0-9]{64}$")


@router.post("/api/v1/applications", status_code=201, response_model=StationApplicationRecord)
def create_application(body: ApplicationInput, user: Actor, db: DB, request: Request):
    authorize_business_write(db, user, request, "applications.create")
    from app.workflows import create

    result = create(db, user, body.station_id, body.kind, body.payload)
    response = StationApplicationRecord.model_validate(named_output(db, result))
    db.commit()
    return response


class ApplicationFilters(BaseModel):
    q: str = Field("", max_length=150)
    status: str = Field("", max_length=30)
    kind: str = Field("", max_length=30)
    application_id: int | None = Field(None, ge=1, le=9223372036854775807)
    station_id: int | None = Field(None, ge=1, le=9223372036854775807)
    submitted_from: date | None = None
    submitted_to: date | None = None


Filters = Annotated[ApplicationFilters, Depends()]


def filtered_applications(user, filters):
    if filters.status not in {"", "draft", "submitted", "rejected", "approved", "withdrawn"}:
        raise HTTPException(422, "不支持的申请状态")
    if filters.kind and filters.kind not in KINDS:
        raise HTTPException(422, "不支持的申请类型")
    stmt = scoped_records(application_rows(), StationApplication, user)
    if user.role == "operator" and user.operator_id is not None:
        stmt = stmt.where(StationApplication.operator_id == user.operator_id)
    elif user.role != "regulator":
        raise HTTPException(403, "无权查看场站申请")
    if query := filters.q.strip():
        stmt = stmt.where(
            or_(
                Station.name.icontains(query, autoescape=True),
                Operator.name.icontains(query, autoescape=True),
            )
        )
    if filters.status:
        stmt = stmt.where(StationApplication.status == filters.status)
    if filters.kind:
        stmt = stmt.where(StationApplication.kind == filters.kind)
    if filters.station_id is not None:
        stmt = stmt.where(StationApplication.station_id == filters.station_id)
    if filters.application_id is not None:
        stmt = stmt.where(StationApplication.id == filters.application_id)
    return apply_beijing_days(stmt, last_submit, filters.submitted_from, filters.submitted_to)


@router.get("/api/v1/applications", response_model=StationApplicationPage)
def applications(
    user: SnapshotActor,
    db: SnapshotDB,
    filters: Filters,
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    stmt = filtered_applications(user, filters)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(
        stmt.order_by(StationApplication.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return {
        "items": [record_output(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get(
    "/api/v1/applications/export",
    response_class=Response,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string"}}}}},
)
def export_applications(user: SnapshotActor, db: SnapshotDB, filters: Filters):
    require(db, user, "applications.read")
    rows = db.execute(
        filtered_applications(user, filters).order_by(StationApplication.id.desc()).limit(10001)
    ).all()
    if len(rows) > 10000:
        raise HTTPException(422, "单次最多导出10000条，请缩小范围")
    labels = {
        "access": "场站接入",
        "acceptance": "竣工验收",
        "commission": "投运申请",
        "decommission": "核销申请",
        "change": "信息变更",
    }
    statuses = {
        "draft": "草稿",
        "submitted": "待审核",
        "rejected": "已驳回",
        "approved": "已通过",
        "withdrawn": "已撤回",
    }
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "申请编号",
            "场站",
            "地址",
            "运营商",
            "申请类型",
            "状态",
            "当前材料份数",
            "最近提交时间(带时区)",
        ]
    )
    for a, name, address, operator, submitted, materials in rows:
        writer.writerow(
            [
                csv_cell(value)
                for value in (
                    f"APP-{a.id}",
                    name,
                    address,
                    operator,
                    labels.get(a.kind, a.kind),
                    statuses.get(a.status, a.status),
                    materials,
                    submitted.isoformat() if submitted else "无提交记录",
                )
            ]
        )
    db.add(Audit(user_id=user.id, action="applications.export", detail={"rows": len(rows)}))
    db.commit()
    return csv_response(buffer.getvalue(), "ronghub-station-applications.csv")


@router.get("/api/v1/applications/{application_id}", response_model=StationApplicationDetail)
def application_detail(application_id: int, user: SnapshotActor, db: SnapshotDB):
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

    result = act(
        db, user, application_id, body.action, body.version, body.comment, body.review_token
    )
    response = StationApplicationRecord.model_validate(named_output(db, result))
    db.commit()
    return response


class DraftEditInput(BaseModel):
    version: int
    note: str = Field(default="", max_length=2000)
    name: str | None = Field(default=None, max_length=150)
    address: str | None = Field(default=None, max_length=255)
    contact_name: str | None = Field(None, max_length=100)
    contact_phone: str | None = Field(None, max_length=80)

    @model_validator(mode="after")
    def contact_pair(self):
        fields = {"contact_name", "contact_phone"}.intersection(self.model_fields_set)
        if fields and len(fields) != 2:
            raise ValueError("申请联系人和联系方式需同时提供或清空")
        if fields:
            value = ApplicationContact(name=self.contact_name, phone=self.contact_phone)
            self.contact_name, self.contact_phone = value.name, value.phone
        return self


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
        .execution_options(populate_existing=True)
    )
    if not a or (user.role == "operator" and a.operator_id != user.operator_id):
        raise HTTPException(404, "申请不存在或不可访问")
    if user.role != "operator":
        raise HTTPException(403, "仅所属运营商可编辑")
    if a.status not in {"draft", "rejected"} or a.version != body.version:
        raise HTTPException(409, "申请已更新或不处于可编辑状态，请刷新")
    before = state(a)
    before_payload = dict(a.payload)
    previous_contact = a.payload.get("contact")
    a.payload = {"note": body.note}
    if "contact_name" in body.model_fields_set:
        a.payload["contact"] = {"name": body.contact_name, "phone": body.contact_phone}
    elif "contact" in before_payload:
        a.payload["contact"] = previous_contact
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
    response = StationApplicationRecord.model_validate(named_output(db, a))
    db.commit()
    return response
