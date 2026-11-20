"""Application-owned contacts, public station projection and immutable submission content.

Review digests detect content changes; they are not authentication or signatures.
Source enterprise/station contacts are never copied into application evidence.
"""

import hashlib
import json
from datetime import UTC

from fastapi import HTTPException
from pydantic import BaseModel, Field, JsonValue, model_validator
from sqlalchemy import cast, select
from sqlalchemy.dialects.postgresql import JSONB

from app.authorization import permissions
from app.models import (
    ApplicationSubmission,
    Attachment,
    Connector,
    Equipment,
    Operator,
    Station,
    StationApplication,
    User,
    now,
)
from app.scopes import records as scoped_records
from app.scopes import station_scope
from app.station_facilities import Facilities, public

CATEGORIES = {
    "project_filing": "项目备案申报表",
    "site_agreement": "场地协议书",
    "property_rights": "不动产权证明",
    "owner_qualification": "产权单位企业资质",
    "agent_identity": "备案经办人员身份证复印件",
    "enterprise_commitment": "企业承诺函",
    "safety_commitment": "安全生产承诺书",
    "land_construction": "建设活动管理、用地等承诺书",
    "site_layout_photos": "场站平面布置图、现场照片",
    "other": "其他材料",
}


class ApplicationContact(BaseModel):
    name: str | None = Field(None, max_length=100)
    phone: str | None = Field(None, max_length=80)

    @model_validator(mode="after")
    def pair(self):
        self.name = self.name.strip() or None if self.name is not None else None
        self.phone = self.phone.strip() or None if self.phone is not None else None
        if bool(self.name) != bool(self.phone):
            raise ValueError("申请联系人和联系方式需同时填写")
        if any(
            ord(c) < 32 or ord(c) == 127
            for value in [self.name, self.phone]
            if value
            for c in value
        ):
            raise ValueError("申请联系信息含无效控制字符")
        return self


def normalize_payload(payload):
    result = dict(payload)
    if "contact" in result:
        result["contact"] = ApplicationContact.model_validate(result["contact"]).model_dump()
    return result


class SourceProfile(BaseModel):
    station_id: int
    station_name: str
    operator_id: int
    operator_name: str
    source: str
    external_id: str
    district: str
    address: str
    platform_status: str
    station_version: int
    source_synced_at: str
    station_type: str
    usage_type: str
    construction_site: str
    site_state: str
    source_sync_state: str
    source_first_connected_at: str | None
    source_first_reported_at: str | None
    power_kw: str
    connectors: int
    longitude: str | None
    latitude: str | None
    coordinate_system: str
    location_is_demo: bool
    facilities: Facilities


class EvidenceMaterial(BaseModel):
    id: int
    filename: str
    content_type: str
    size: int = Field(ge=0)
    sha256: str
    created_at: str
    category: str | None


class EvidenceConnector(BaseModel):
    id: int
    external_id: str
    name: str | None
    interface_type: str | None


class EvidenceEquipment(BaseModel):
    id: int
    external_id: str
    name: str
    power_kw: str
    equipment_type: str | None
    model_name: str | None
    source_unique_code: str | None
    manufactured_on: str | None
    connectors: list[EvidenceConnector]


class ReviewContent(BaseModel):
    schema_version: int
    source_profile: SourceProfile | None
    payload: dict[str, JsonValue]
    materials: list[EvidenceMaterial]
    # None means an older submission did not capture inventory, never an empty fleet.
    equipment: list[EvidenceEquipment] | None = None


class ReviewContext(BaseModel):
    application_id: int
    application_version: int
    source_accessible: bool
    review_token: str | None
    content: ReviewContent
    category_options: dict[str, str]
    notice: str


class SubmissionItem(BaseModel):
    id: int
    application_id: int
    submitted_version: int
    submitted_at: str
    actor_id: int
    actor_username: str
    source_reviewed: bool
    material_count: int


class SubmissionPage(BaseModel):
    items: list[SubmissionItem]
    total: int
    page: int
    page_size: int


class SubmissionDetail(SubmissionItem):
    source_accessible: bool
    content_digest: str | None
    content: ReviewContent
    notice: str


def access(db, user, aid, lock=False):
    a = db.scalar(
        scoped_records(select(StationApplication), StationApplication, user)
        .where(StationApplication.id == aid)
        .with_for_update()
        .execution_options(populate_existing=True)
        if lock
        else scoped_records(select(StationApplication), StationApplication, user).where(
            StationApplication.id == aid
        )
    )
    if not a or (user.role == "operator" and a.operator_id != user.operator_id):
        raise HTTPException(404, "申请不存在或不可访问")
    if user.role not in {"operator", "regulator"}:
        raise HTTPException(403, "无权查看申请送审资料")
    return a


def source_profile(db, user, a, lock=False):
    stmt = station_scope(select(Station), user).where(Station.id == a.station_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    s = db.scalar(stmt)
    if not s:
        raise HTTPException(404, "场站不存在或不在授权区域")
    iso = lambda value: value.astimezone(UTC).isoformat() if value is not None else None
    return SourceProfile.model_validate(
        {
            "station_id": s.id,
            "station_name": s.name,
            "operator_id": a.operator_id,
            "operator_name": db.scalar(select(Operator.name).where(Operator.id == a.operator_id)),
            "source": s.source,
            "external_id": s.external_id,
            "district": s.district,
            "address": s.address,
            "platform_status": s.status,
            "station_version": s.version,
            "source_synced_at": iso(s.synced_at),
            "station_type": s.station_type,
            "usage_type": s.usage_type,
            "construction_site": s.construction_site,
            "site_state": s.site_state,
            "source_sync_state": s.source_sync_state,
            "source_first_connected_at": iso(s.source_first_connected_at),
            "source_first_reported_at": iso(s.source_first_reported_at),
            "power_kw": str(s.power_kw),
            "connectors": s.connectors,
            "longitude": str(s.longitude) if s.longitude is not None else None,
            "latitude": str(s.latitude) if s.latitude is not None else None,
            "coordinate_system": s.coordinate_system,
            "location_is_demo": s.location_is_demo,
            "facilities": public(s),
        }
    )


def material(row):
    return {
        "id": row.id,
        "filename": row.filename,
        "content_type": row.content_type,
        "size": row.size,
        "sha256": row.sha256,
        "created_at": row.created_at.astimezone(UTC).isoformat(),
        "category": row.category,
    }


def equipment_snapshot(db, a, lock=False):
    stmt = select(Equipment).where(Equipment.station_id == a.station_id).order_by(Equipment.id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    equipment = list(db.scalars(stmt))
    connectors_stmt = (
        select(Connector)
        .join(Equipment, Equipment.id == Connector.equipment_id)
        .where(Equipment.station_id == a.station_id)
        .order_by(Connector.id)
    )
    if lock:
        connectors_stmt = connectors_stmt.with_for_update(of=Connector).execution_options(
            populate_existing=True
        )
    connectors = list(db.scalars(connectors_stmt))
    groups = {}
    for c in connectors:
        groups.setdefault(c.equipment_id, []).append(
            {
                "id": c.id,
                "external_id": c.external_id,
                "name": c.name,
                "interface_type": c.interface_type,
            }
        )
    return [
        {
            "id": e.id,
            "external_id": e.external_id,
            "name": e.name,
            "power_kw": str(e.power_kw),
            "equipment_type": e.equipment_type,
            "model_name": e.model_name,
            "source_unique_code": e.source_unique_code,
            "manufactured_on": e.manufactured_on.isoformat() if e.manufactured_on else None,
            "connectors": groups.get(e.id, []),
        }
        for e in equipment
    ]


def content(db, user, a, lock=False):
    profile = source_profile(db, user, a, lock)
    files = list(
        db.scalars(
            select(Attachment)
            .where(Attachment.application_id == a.id, Attachment.active.is_(True))
            .order_by(Attachment.id)
        )
    )
    return ReviewContent.model_validate(
        {
            "schema_version": 2,
            "source_profile": profile,
            "payload": a.payload,
            "materials": [material(f) for f in files],
            "equipment": equipment_snapshot(db, a, lock),
        }
    )


def digest(value):
    body = value.model_dump(mode="json")
    # A successful repeat sync with unchanged content must not invalidate review
    # merely because its receipt timestamp advanced. The frozen copy retains it.
    if body["source_profile"] is not None:
        body["source_profile"].pop("source_synced_at", None)
    try:
        encoded = json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    except (TypeError, ValueError) as error:
        raise HTTPException(422, "申请内容含无法归档的数据，请先修正") from error
    return hashlib.sha256(encoded).hexdigest()


def context(db, user, a):
    value = content(db, user, a)
    can_source = "stations.read" in permissions(db, user)
    token = digest(value) if can_source else None
    if not can_source:
        value.source_profile = None
        value.equipment = None
    return ReviewContext(
        application_id=a.id,
        application_version=a.version,
        source_accessible=can_source,
        review_token=token,
        content=value,
        category_options=CATEGORIES,
        notice="当前来源资料仅供核对；每次提交另行冻结。来源联系信息不复制，本申请联系信息由申请人填写。材料分类为原型候选，正式必填、格式及配额待确认；当前仍PDF/PNG/JPEG、每份5MiB、最多10份。仅同步时间变化不要求重复核对；来源内容、申请或材料变化时需重新读取后提交。",
    )


def capture(db, user, a, expected_token=None):
    value = content(db, user, a, lock=True)
    token = digest(value)
    can_source = "stations.read" in permissions(db, user)
    if expected_token is not None and (not can_source or token != expected_token):
        raise HTTPException(409, "来源资料、申请或材料已更新，请重新核对送审资料后提交")
    stamp = now()
    row = ApplicationSubmission(
        application_id=a.id,
        submitted_version=a.version + 1,
        actor_id=user.id,
        actor_username=db.scalar(select(User.username).where(User.id == user.id)),
        submitted_at=stamp,
        source_reviewed=expected_token is not None,
        content_digest=token,
        snapshot=value.model_dump(mode="json"),
    )
    db.add(row)
    db.flush()
    return stamp


def submission_item(row):
    return {
        "id": row.id,
        "application_id": row.application_id,
        "submitted_version": row.submitted_version,
        "submitted_at": row.submitted_at.isoformat(),
        "actor_id": row.actor_id,
        "actor_username": row.actor_username,
        "source_reviewed": row.source_reviewed,
        "material_count": len(row.snapshot["materials"]),
    }


def submission_detail(db, user, row):
    value = ReviewContent.model_validate(row.snapshot)
    can_source = "stations.read" in permissions(db, user)
    if not can_source:
        value.source_profile = None
        value.equipment = None
    return SubmissionDetail(
        **submission_item(row),
        source_accessible=can_source,
        content_digest=row.content_digest if can_source else None,
        content=value,
        notice="本次提交时冻结的来源资料、申请内容与材料清单；后续来源、联系人或材料分类变化不改写此版本。旧申请未留存送审版本时不使用当前资料回填。来源资料仍按场站读取权限显示，历史材料下载独立鉴权。",
    )


def material_was_submitted(db, attachment):
    return (
        db.scalar(
            select(ApplicationSubmission.id)
            .where(
                ApplicationSubmission.application_id == attachment.application_id,
                cast(ApplicationSubmission.snapshot["materials"], JSONB).contains(
                    [{"id": attachment.id}]
                ),
            )
            .limit(1)
        )
        is not None
    )
