"""Independent demo hazard supervision: completion never closes regulatory tracking."""

from datetime import datetime
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from app.authorization import require
from app.business_audit import transition
from app.config import settings
from app.inspection_documents import ExecutionContact, prepare, requirement
from app.material_contracts import InspectionMaterialItem
from app.models import HazardAction, HazardAttachment, HazardCase, Operator, Station, now
from app.richtext import RichDocument, image_ids
from app.scopes import records, station_scope


class HazardCreateInput(BaseModel):
    station_id: int = Field(ge=1)
    operator_id: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=150)
    description: str = Field(min_length=1, max_length=4000)
    rich_document: RichDocument | None = None
    severity: Literal["normal", "major"]
    due_at: datetime


class HazardActionInput(BaseModel):
    version: int = Field(ge=1)
    action: Literal["start", "reply", "close"]
    comment: str = Field(min_length=1, max_length=4000)
    rich_document: RichDocument | None = None
    contact: ExecutionContact | None = None
    performed_at: datetime | None = None


class HazardSummary(BaseModel):
    id: int
    station_id: int
    operator_id: int
    station_name: str
    operator_name: str
    title: str
    description: str
    rich_document: RichDocument | None
    severity: Literal["normal", "major"]
    due_at: datetime
    status: Literal["tracking", "closed"]
    progress: Literal["not_started", "in_progress", "completed"]
    version: int
    created_at: datetime
    closed_at: datetime | None
    overdue: bool
    is_demo: bool


class HazardHistory(BaseModel):
    id: int
    action: Literal["publish", "start", "reply", "close"]
    comment: str
    rich_document: RichDocument | None
    contact: ExecutionContact | None
    performed_at: datetime | None
    created_at: datetime


class HazardDetail(HazardSummary):
    history: list[HazardHistory]
    materials: list[InspectionMaterialItem]


class HazardPage(BaseModel):
    items: list[HazardSummary]
    total: int
    page: int
    page_size: int
    query_at: datetime


class HazardCandidate(BaseModel):
    id: int
    name: str
    operator_id: int
    operator_name: str
    district: str


class HazardCandidates(BaseModel):
    items: list[HazardCandidate]
    total: int
    page: int
    page_size: int


def demo_only():
    if not settings.demo_mode:
        raise HTTPException(409, "隐患督办写入仅限本地演示，正式职责规则及接口待确认")


def scoped(user):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "当前身份无权查看隐患督办")
    stmt = records(select(HazardCase), HazardCase, user)
    if user.role == "operator":
        stmt = stmt.where(HazardCase.operator_id == user.operator_id)
    return stmt


def access(db, user, case_id, *, lock=False):
    stmt = scoped(user).where(HazardCase.id == case_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(stmt)
    if row is None:
        raise HTTPException(404, "隐患不存在或不可访问")
    return row


def summary(row, at=None):
    at = at or now()
    result = {field: getattr(row, field) for field in HazardSummary.model_fields if field != "overdue"}
    result["overdue"] = row.status == "tracking" and row.progress != "completed" and row.due_at < at
    return result


def output(db, row):
    from app.routers.attachments import metadata

    history = db.scalars(
        select(HazardAction).where(HazardAction.case_id == row.id).order_by(HazardAction.id)
    )
    materials = db.scalars(
        select(HazardAttachment)
        .where(HazardAttachment.case_id == row.id)
        .order_by(HazardAttachment.id)
    )
    return HazardDetail.model_validate(
        {
            **summary(row),
            "history": [
                {
                    "id": action.id,
                    "action": action.action,
                    "comment": action.comment,
                    "rich_document": action.rich_document,
                    "contact": {"name": action.contact_name, "phone": action.contact_phone}
                    if action.contact_name is not None
                    else None,
                    "performed_at": action.performed_at,
                    "created_at": action.created_at,
                }
                for action in history
            ],
            "materials": [{**metadata(item), "action_id": item.action_id} for item in materials],
        }
    )


def audit_state(row):
    return {
        "station_id": row.station_id,
        "operator_id": row.operator_id,
        "status": row.status,
        "progress": row.progress,
        "version": row.version,
        "severity": row.severity,
        "due_at": row.due_at.isoformat(),
        "is_demo": row.is_demo,
    }


def create(db, user, body):
    demo_only()
    require(db, user, "hazards.create")
    if user.role != "regulator":
        raise HTTPException(403, "仅监管人员可登记督办")
    station = db.scalar(
        station_scope(select(Station), user).where(Station.id == body.station_id).with_for_update()
    )
    if station is None:
        raise HTTPException(404, "场站不存在或超出授权范围")
    # Cross-company delegation needs an agreed disclosure boundary; do not expose station data.
    if body.operator_id != station.operator_id:
        raise HTTPException(422, "目标运营商须为所选场站当前归属企业；跨企业委派规则待确认")
    company = db.get(Operator, body.operator_id)
    if company is None or not company.enabled:
        raise HTTPException(422, "目标运营商不存在或已停用")
    if body.due_at.tzinfo is None or body.due_at <= now():
        raise HTTPException(422, "整改期限需带时区且晚于当前时间")
    description, document = requirement(body.description, body.rich_document)
    if not body.title.strip():
        raise HTTPException(422, "请填写隐患名称")
    row = HazardCase(
        station_id=station.id,
        operator_id=company.id,
        station_name=station.name,
        operator_name=company.name,
        title=body.title.strip(),
        description=description,
        rich_document=document,
        severity=body.severity,
        due_at=body.due_at,
        status="tracking",
        progress="not_started",
        version=1,
        created_by=user.id,
    )
    db.add(row)
    db.flush()
    db.add(
        HazardAction(
            case_id=row.id,
            actor_id=user.id,
            action="publish",
            comment=description,
            rich_document=document,
        )
    )
    transition(db, user, "hazards.create", row.id, None, audit_state(row), "hazard_case")
    db.flush()
    return row


def act(db, user, case_id, body):
    demo_only()
    require(db, user, "hazards.close" if body.action == "close" else "hazards.reply")
    row = access(db, user, case_id, lock=True)
    if row.version != body.version:
        raise HTTPException(409, "隐患已更新，请刷新后重试")
    if row.status == "closed":
        raise HTTPException(409, "督办已关闭，不能继续整改或提交材料")
    if (body.action == "close" and user.role != "regulator") or (
        body.action != "close" and user.role != "operator"
    ):
        raise HTTPException(403, "当前身份不能办理该督办动作")
    comment, document = prepare(body.comment, body.rich_document)
    before = audit_state(row)
    if body.action == "reply":
        if (
            body.contact is None
            or not body.contact.name
            or not body.contact.phone
            or body.performed_at is None
        ):
            raise HTTPException(422, "整改反馈需填写执行联系人、联系方式及完成时间")
        if body.performed_at.tzinfo is None or body.performed_at > now():
            raise HTTPException(422, "完成时间需带时区且不能晚于当前时间")
        pending = list(
            db.scalars(
                select(HazardAttachment).where(
                    HazardAttachment.case_id == row.id, HazardAttachment.action_id.is_(None)
                )
            )
        )
        if not pending:
            raise HTTPException(422, "本次整改反馈至少需一份未提交证明材料")
        allowed = {item.id for item in pending if item.content_type in {"image/png", "image/jpeg"}}
        if document is not None and not image_ids(document).issubset(allowed):
            raise HTTPException(422, "反馈图片须来自本隐患本次未提交图片材料")
        row.progress = "completed"
    else:
        if (
            body.contact is not None
            or body.performed_at is not None
            or (document is not None and image_ids(document))
        ):
            raise HTTPException(422, "执行事实及材料图片仅用于企业整改反馈")
        if body.action == "start":
            if row.progress != "not_started":
                raise HTTPException(409, "仅尚未开始的隐患可开始整改")
            row.progress = "in_progress"
        else:
            # Prototype explicitly permits regulator closure independently of completion.
            row.status = "closed"
            row.closed_at = now()
    row.version += 1
    action = HazardAction(
        case_id=row.id,
        actor_id=user.id,
        action=body.action,
        comment=comment,
        rich_document=document,
        contact_name=body.contact.name if body.contact else None,
        contact_phone=body.contact.phone if body.contact else None,
        performed_at=body.performed_at,
    )
    db.add(action)
    db.flush()
    if body.action == "reply":
        db.execute(
            update(HazardAttachment)
            .where(HazardAttachment.case_id == row.id, HazardAttachment.action_id.is_(None))
            .values(action_id=action.id)
        )
    transition(db, user, "hazards." + body.action, row.id, before, audit_state(row), "hazard_case")
    return row
