"""Annual multi-station creation with atomic claims and durable retry receipts."""

import hashlib
import json
import secrets
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, Json, model_validator
from sqlalchemy import func, select

from app import subsidy
from app import subsidy_evidence as evidence
from app.authorization import authorize_business_write, require
from app.business_contracts import SubsidyAnnualResult, SubsidyAnnualStations
from app.config import settings
from app.dependencies import DB, Actor
from app.models import Audit, Equipment, Station, SubsidyAnnualBatch, SubsidyAttachment
from app.reporting import SnapshotDB
from app.scopes import station_scope
from app.storage import store_upload

router = APIRouter(prefix="/api/v1/subsidy", tags=["subsidy"])
MAX_STATIONS = 20
MAX_BATCH_BYTES = 100 * 1024 * 1024
RECORDED = ("access_approved", "accepted", "operating")
IntId = Annotated[int, Field(strict=True, ge=1, le=2147483647)]


def current_year():
    return datetime.now(ZoneInfo("Asia/Shanghai")).year


class SubsidyAnnualInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID
    application_year: Annotated[int, Field(strict=True, ge=2000, le=9998)]
    policy_id: IntId
    station_ids: list[IntId] = Field(min_length=1, max_length=MAX_STATIONS)
    application_note: str = Field(default="", max_length=2000)
    mode: Literal["draft", "submit"] = "draft"

    @model_validator(mode="after")
    def validate_annual(self):
        if len(set(self.station_ids)) != len(self.station_ids):
            raise ValueError("场站不能重复选择")
        if self.application_year > current_year():
            raise ValueError("补贴年度不能晚于当前北京时间年度")
        evidence.validate_note(self.application_note)
        return self


def equipment_totals():
    return (
        select(
            Equipment.station_id.label("station_id"),
            func.count().label("equipment_count"),
            func.sum(Equipment.power_kw).label("equipment_power_kw"),
        )
        .group_by(Equipment.station_id)
        .subquery()
    )


@router.get("/annual-stations", response_model=SubsidyAnnualStations)
def stations(
    user: Actor,
    db: SnapshotDB,
    q: str = Query("", max_length=150),
    district: str = Query("", max_length=80),
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    if user.role != "operator":
        raise HTTPException(403, "仅所属运营商可选择申报场站")
    totals = equipment_totals()
    stmt = station_scope(
        select(Station, totals.c.equipment_count, totals.c.equipment_power_kw)
        .outerjoin(totals, totals.c.station_id == Station.id)
        .where(Station.status.in_(RECORDED)),
        user,
    )
    if q.strip():
        stmt = stmt.where(Station.name.icontains(q.strip(), autoescape=True))
    if district.strip():
        stmt = stmt.where(Station.district == district.strip())
    count = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.order_by(Station.id).offset((page - 1) * page_size).limit(page_size))
    items = []
    for station, terminals, power in rows:
        reason = (
            "无终端，不可选择"
            if not terminals
            else "当前演示核算要求已投运；正式备案资格待确认"
            if station.status != "operating"
            else None
        )
        items.append(
            {
                "id": station.id,
                "name": station.name,
                "district": station.district,
                "status": station.status,
                "equipment_count": terminals or 0,
                "equipment_power_kw": str(power) if power is not None else None,
                "selectable": reason is None,
                "unavailable_reason": reason,
            }
        )
    year = current_year()
    return {
        "items": items,
        "total": count,
        "page": page,
        "page_size": page_size,
        "default_year": year - 1,
        "current_year": year,
    }


def ensure_stations(db, user, ids):
    rows = list(
        db.scalars(
            station_scope(select(Station), user)
            .where(Station.id.in_(ids))
            .order_by(Station.id)
            .with_for_update()
        )
    )
    if len(rows) != len(ids):
        raise HTTPException(404, "部分场站不存在或不可访问")
    terminals = dict(
        db.execute(
            select(Equipment.station_id, func.count())
            .where(Equipment.station_id.in_(ids))
            .group_by(Equipment.station_id)
        ).all()
    )
    for station in rows:
        if station.status != "operating":
            raise HTTPException(409, "所选场站未满足当前演示已投运条件")
        if not terminals.get(station.id):
            raise HTTPException(409, "所选场站无终端，不能申报")


@router.post("/annual-batches", status_code=201, response_model=SubsidyAnnualResult)
def create(
    user: Actor,
    db: DB,
    request: Request,
    payload: Annotated[Json[SubsidyAnnualInput], Form()],
    files: Annotated[list[UploadFile] | None, File()] = None,
):
    authorize_business_write(db, user, request, "subsidy.create")
    subsidy.demo_only()
    if user.role != "operator" or user.operator_id is None:
        raise HTTPException(403, "仅所属运营商可年度申报")
    uploads = files or []
    if uploads:
        require(db, user, "subsidy.materials")
    if payload.mode == "submit":
        require(db, user, "subsidy.submit")
        if not payload.application_note.strip() or not uploads:
            raise HTTPException(422, "提交须有申请说明和ZIP申报材料")
    if len(uploads) > evidence.MAX_FILES:
        raise HTTPException(422, "每份申请最多10份ZIP材料")
    paths = []
    committed = False
    try:
        material_values = []
        total_bytes = 0
        for upload in uploads:
            values, path = store_upload(
                upload,
                max_bytes=evidence.LIMIT - total_bytes,
                allow_archives=True,
                allowed_types={"application/zip"},
            )
            paths.append(path)
            total_bytes += values["size"]
            material_values.append(values)
        if total_bytes * len(payload.station_ids) > MAX_BATCH_BYTES:
            raise HTTPException(413, "按场站独立存储后的整批材料不能超过100MiB")
        normalized = payload.model_dump(mode="json", exclude={"request_key"})
        normalized["station_ids"] = sorted(payload.station_ids)
        normalized["materials"] = [
            {key: item[key] for key in ("filename", "content_type", "size", "sha256")}
            for item in material_values
        ]
        fingerprint = hashlib.sha256(
            json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        existing = db.scalar(
            select(SubsidyAnnualBatch).where(
                SubsidyAnnualBatch.actor_id == user.id,
                SubsidyAnnualBatch.request_key == str(payload.request_key),
            )
        )
        if existing:
            if existing.fingerprint != fingerprint:
                raise HTTPException(409, "该请求编号已用于不同内容，请核对原申报回执")
            for item in existing.receipt["items"]:
                subsidy.claim_access(db, user, item["id"])
            result = SubsidyAnnualResult.model_validate(
                {"replayed": True, "receipt": existing.receipt}
            )
            db.rollback()
            return result
        ensure_stations(db, user, normalized["station_ids"])
        year = payload.application_year
        start, end = date(year, 1, 1), date(year, 12, 31)
        claims = []
        for index, station_id in enumerate(normalized["station_ids"]):
            claim = subsidy.create_claim(
                db, user, station_id, payload.policy_id, start, end, payload.application_note
            )
            claim.snapshot = {**claim.snapshot, "application_year": year}
            for original in material_values:
                values = dict(original)
                if index:
                    body = evidence.content(SimpleNamespace(**original))
                    key = secrets.token_hex(24)
                    path = Path(settings.storage_path).resolve() / key
                    with path.open("xb") as target:
                        paths.append(path)
                        path.chmod(0o600)
                        target.write(body)
                    values["storage_key"] = key
                db.add(SubsidyAttachment(claim_id=claim.id, uploaded_by=user.id, **values))
            db.flush()
            if payload.mode == "submit":
                subsidy.act(db, user, claim.id, "submit", claim.version, "年度多场站申报")
            claims.append(subsidy.output(claim))
        batch = SubsidyAnnualBatch(
            actor_id=user.id,
            operator_id=user.operator_id,
            request_key=str(payload.request_key),
            fingerprint=fingerprint,
            receipt={},
        )
        db.add(batch)
        db.flush()
        result = SubsidyAnnualResult.model_validate(
            {
                "replayed": False,
                "receipt": {
                    "batch_id": batch.id,
                    "application_year": year,
                    "mode": payload.mode,
                    "items": claims,
                },
            }
        )
        batch.receipt = result.receipt.model_dump(mode="json")
        db.add(
            Audit(
                user_id=user.id,
                action="subsidy.annual_create",
                object_id=batch.id,
                detail={"count": len(claims), "application_year": year, "mode": payload.mode},
            )
        )
        db.commit()
        committed = True
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        if not committed:
            for path in paths:
                path.unlink(missing_ok=True)
        for upload in uploads:
            upload.file.close()
