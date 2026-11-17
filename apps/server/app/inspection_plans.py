"""Private preparation and atomic per-station publication of inspection plans."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app import inspection
from app.authorization import require
from app.business_audit import state, transition
from app.inspection_documents import requirement
from app.models import InspectionPlan, InspectionTask, Operator, Station, now
from app.richtext import RichDocument
from app.scopes import station_scope

StationId = Annotated[int, Field(ge=1, le=2147483647)]


class InspectionPlanCreate(BaseModel):
    request_key: UUID


class InspectionPlanEdit(BaseModel):
    version: int = Field(ge=1)
    title: str = Field(max_length=150)
    description: str = Field(max_length=4000)
    rich_document: RichDocument | None = None
    station_ids: list[StationId] = Field(max_length=100)
    due_at: datetime | None

    @model_validator(mode="after")
    def unique_targets(self):
        if len(self.station_ids) != len(set(self.station_ids)):
            raise ValueError("同一计划不能重复选择场站")
        if self.due_at is not None and (
            self.due_at.tzinfo is None or self.due_at.utcoffset() is None
        ):
            raise ValueError("任务期限需带时区，未知可不设置")
        return self


class InspectionPlanVersion(BaseModel):
    version: int = Field(ge=1)


class InspectionPlanTarget(BaseModel):
    station_id: int
    station_name: str | None
    operator_name: str | None
    district: str | None
    accessible: bool
    recipient_changed: bool | None
    task_id: int | None
    task_status: str | None


class InspectionPlanSummary(BaseModel):
    id: int
    title: str
    status: Literal["draft", "published", "deleted"]
    version: int
    target_count: int
    published_count: int
    due_at: str | None
    created_at: str
    updated_at: str
    published_at: str | None


class InspectionPlanDetail(InspectionPlanSummary):
    description: str
    rich_document: RichDocument | None
    targets: list[InspectionPlanTarget]


class InspectionPlanPage(BaseModel):
    items: list[InspectionPlanSummary]
    total: int
    page: int
    page_size: int


class InspectionPlanStationOption(BaseModel):
    id: int
    name: str
    operator_name: str
    district: str


class InspectionPlanStationPage(BaseModel):
    items: list[InspectionPlanStationOption]
    total: int
    page: int
    page_size: int


def planner(db, user):
    require(db, user, "inspections.read")
    require(db, user, "inspections.assign")
    if user.role != "regulator":
        raise HTTPException(403, "仅监管下发岗位可管理自己的巡检计划")


def access(db, user, identifier, lock=False):
    planner(db, user)
    stmt = select(InspectionPlan).where(
        InspectionPlan.id == identifier, InspectionPlan.created_by == user.id
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(stmt)
    if not row:
        raise HTTPException(404, "计划不存在或不属于当前创建者")
    return row


def current_targets(db, user, ids, lock=False):
    stmt = (
        station_scope(
            select(Station, Operator.name).join(Operator, Operator.id == Station.operator_id), user
        )
        .where(Station.id.in_(ids))
        .order_by(Station.id)
    )
    if lock:
        stmt = stmt.with_for_update(of=Station).execution_options(populate_existing=True)
    return {station.id: (station, name) for station, name in db.execute(stmt)}


def scope_value(station):
    return {
        "station_id": station.id,
        "operator_id": station.operator_id,
        "district": station.district,
    }


def summary(row):
    return {
        "id": row.id,
        "title": row.title,
        "status": row.status,
        "version": row.version,
        "target_count": len(row.targets),
        "published_count": len(row.targets) if row.status == "published" else 0,
        "due_at": row.due_at.isoformat() if row.due_at is not None else None,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "published_at": row.published_at.isoformat() if row.published_at is not None else None,
    }


def output(db, user, row):
    current = current_targets(db, user, [value["station_id"] for value in row.targets])
    tasks = {
        task.station_id: task
        for task in db.scalars(select(InspectionTask).where(InspectionTask.plan_id == row.id))
    }
    targets = []
    for saved in row.targets:
        match = current.get(saved["station_id"])
        station, company = match if match is not None else (None, None)
        task = tasks.get(saved["station_id"]) if match is not None else None
        targets.append(
            {
                "station_id": saved["station_id"],
                "station_name": station.name if station is not None else None,
                "operator_name": company,
                "district": station.district if station is not None else None,
                "accessible": match is not None,
                "recipient_changed": scope_value(station) != saved if station is not None else None,
                "task_id": task.id if task is not None else None,
                "task_status": task.status if task is not None else None,
            }
        )
    return {
        **summary(row),
        "description": row.description,
        "rich_document": row.rich_document,
        "targets": targets,
    }


def create(db, user, request_key):
    inspection.demo_only()
    planner(db, user)
    identifier = db.scalar(
        insert(InspectionPlan)
        .values(created_by=user.id, request_key=str(request_key))
        .on_conflict_do_nothing(constraint="uq_inspection_plan_request")
        .returning(InspectionPlan.id)
    )
    if identifier is None:
        identifier = db.scalar(
            select(InspectionPlan.id).where(
                InspectionPlan.created_by == user.id, InspectionPlan.request_key == str(request_key)
            )
        )
        return access(db, user, identifier, lock=True)
    row = access(db, user, identifier, lock=True)
    transition(db, user, "inspections.plan.create", row.id, None, state(row), "inspection_plan")
    return row


def edit(db, user, identifier, body):
    inspection.demo_only()
    row = access(db, user, identifier, lock=True)
    if row.status != "draft" or row.version != body.version:
        raise HTTPException(409, "草稿状态或版本已变化，请重新读取")
    current = current_targets(db, user, body.station_ids, lock=True)
    if len(current) != len(body.station_ids):
        raise HTTPException(404, "选择中有场站不存在或不在当前授权范围，未保存")
    if body.description.strip() or body.rich_document is not None:
        description, document = requirement(body.description, body.rich_document)
    else:
        description, document = "", None
    before = state(row)
    row.title = body.title.strip()
    row.description = description
    row.rich_document = document
    row.targets = [scope_value(current[identifier][0]) for identifier in sorted(current)]
    row.due_at = body.due_at
    row.version += 1
    row.updated_at = now()
    transition(db, user, "inspections.plan.save", row.id, before, state(row), "inspection_plan")
    db.flush()
    return row


def publish(db, user, identifier, version):
    inspection.demo_only()
    row = access(db, user, identifier, lock=True)
    if row.status == "published" and version == row.version - 1:
        return row
    if row.status != "draft" or row.version != version:
        raise HTTPException(409, "计划状态或版本已变化，请重新读取")
    if not row.title.strip() or not row.description.strip() or not row.targets:
        raise HTTPException(422, "发布前需保存标题、巡检要求及至少一个目标场站")
    if row.due_at is not None and row.due_at <= now():
        raise HTTPException(422, "计划期限已过，请更新后再发布；也可不设置期限")
    current = current_targets(db, user, [saved["station_id"] for saved in row.targets], lock=True)
    if len(current) != len(row.targets):
        raise HTTPException(409, "目标中有场站不可访问，未下发任何任务，请修改草稿")
    if any(scope_value(current[saved["station_id"]][0]) != saved for saved in row.targets):
        raise HTTPException(409, "目标归属或区域已变化，未下发任何任务，请重新保存核对")
    document = (
        RichDocument.model_validate(row.rich_document) if row.rich_document is not None else None
    )
    before = state(row)
    for saved in row.targets:
        task = inspection.create(
            db,
            user,
            saved["station_id"],
            row.title,
            row.description,
            "normal",
            row.due_at,
            document,
            allow_no_deadline=True,
        )
        task.plan_id = row.id
    stamp = now()
    row.status = "published"
    row.version += 1
    row.published_at = stamp
    row.updated_at = stamp
    transition(db, user, "inspections.plan.publish", row.id, before, state(row), "inspection_plan")
    db.flush()
    return row


def remove(db, user, identifier, version):
    inspection.demo_only()
    row = access(db, user, identifier, lock=True)
    if row.status != "draft" or row.version != version:
        raise HTTPException(409, "仅可删除版本未变化的未发布草稿")
    before = state(row)
    row.status = "deleted"
    row.version += 1
    row.updated_at = now()
    transition(db, user, "inspections.plan.delete", row.id, before, state(row), "inspection_plan")
    db.flush()
    return row
