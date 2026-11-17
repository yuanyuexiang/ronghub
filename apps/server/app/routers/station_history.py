"""Exact-station independent energy and private platform workflow observations."""

from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.authorization import require
from app.dependencies import SnapshotActor
from app.models import ApprovalAction, EnergyRecord, Station, StationApplication, User, now
from app.reporting import SnapshotDB
from app.routers.device_inventory import station_access
from app.scopes import records as scoped_records

router = APIRouter(prefix="/api/v1/stations", tags=["station-history"])


class StationEnergyDay(BaseModel):
    day: str
    kwh: str | None
    records: int
    excluded_records: int


class StationEnergySource(BaseModel):
    source: str
    kwh: str | None
    records: int
    excluded_records: int
    observed_days: int
    missing_days: int
    extra_records_on_observed_days: int
    days: list[StationEnergyDay]


class StationEnergyHistory(BaseModel):
    station_id: int
    station_name: str
    start_date: str
    end_date: str
    requested_days: int
    timezone: Literal["Asia/Shanghai"]
    streams: list[StationEnergySource]
    generated_at: str
    source_completeness_verified: Literal[False]
    notice: str


@router.get("/{station_id}/energy-history", response_model=StationEnergyHistory)
def energy_history(
    station_id: int,
    start_date: date,
    end_date: date,
    user: SnapshotActor,
    db: SnapshotDB,
    source: str = Query("", max_length=50),
):
    require(db, user, "statistics.read")
    station_access(db, user, station_id)
    if (
        start_date > end_date
        or (end_date - start_date).days > 365
        or start_date.year < 1900
        or end_date.year > 9998
    ):
        raise HTTPException(422, "历史电量日期需为1900～9998年内的1至366天")
    generated_at = now()
    station = db.get(Station, station_id)
    conditions = [
        EnergyRecord.station_id == station_id,
        EnergyRecord.day.between(start_date, end_date),
    ]
    if source:
        conditions.append(EnergyRecord.source == source)
    source_count = db.scalar(
        select(func.count(func.distinct(EnergyRecord.source))).where(*conditions)
    )
    if source_count > 20:
        raise HTTPException(422, "当前窗口超过20种来源，请指定电量来源后查询")
    rows = db.execute(
        select(
            EnergyRecord.source,
            EnergyRecord.day,
            func.sum(EnergyRecord.kwh).filter(EnergyRecord.eligible.is_(True)),
            func.count().filter(EnergyRecord.eligible.is_(True)),
            func.count().filter(EnergyRecord.eligible.is_(False)),
        )
        .where(*conditions)
        .group_by(EnergyRecord.source, EnergyRecord.day)
        .order_by(EnergyRecord.source, EnergyRecord.day)
    ).all()
    grouped = {}
    for key, day, kwh, count, excluded in rows:
        grouped.setdefault(key, {})[day] = (kwh, count, excluded)
    source_totals = dict(
        db.execute(
            select(
                EnergyRecord.source,
                func.sum(EnergyRecord.kwh).filter(EnergyRecord.eligible.is_(True)),
            )
            .where(*conditions)
            .group_by(EnergyRecord.source)
        ).all()
    )
    length = (end_date - start_date).days + 1
    streams = []
    for key, days in grouped.items():
        observed = sum(1 for kwh, count, excluded in days.values() if count)
        records = sum(count for kwh, count, excluded in days.values())
        total = source_totals[key] if records else None
        streams.append(
            {
                "source": key,
                "kwh": str(total) if total is not None else None,
                "records": records,
                "excluded_records": sum(v[2] for v in days.values()),
                "observed_days": observed,
                "missing_days": length - observed,
                "extra_records_on_observed_days": records - observed,
                "days": [
                    {
                        "day": (start_date + timedelta(days=i)).isoformat(),
                        "kwh": str(days[start_date + timedelta(days=i)][0])
                        if days.get(start_date + timedelta(days=i), (None, 0, 0))[1]
                        else None,
                        "records": days.get(start_date + timedelta(days=i), (None, 0, 0))[1],
                        "excluded_records": days.get(start_date + timedelta(days=i), (None, 0, 0))[
                            2
                        ],
                    }
                    for i in range(length)
                ],
            }
        )
    return {
        "station_id": station.id,
        "station_name": station.name,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "requested_days": length,
        "timezone": "Asia/Shanghai",
        "streams": streams,
        "generated_at": generated_at.isoformat(),
        "source_completeness_verified": False,
        "notice": "按当前授权场站ID查询独立日电量，业务日直接采用来源日期。各来源分列，不由订单或小时计量补造；只汇总已标记参与统计的记录，未参与记录数另列。缺观测为未知，明确的零保留。同日多条参与记录显示额外条数，不推断为重复或冲正。观测天数不代表完整源覆盖。",
    }


class StationFlowApplication(BaseModel):
    id: int
    kind: str
    status: str
    applicant_id: int
    applicant_username: str
    created_at: str
    last_submitted_at: str | None
    last_approved_at: str | None
    action_count: int


class StationFlowPage(BaseModel):
    station_id: int
    station_name: str
    platform_status: str
    source_sync_state: str
    source_first_connected_at: str | None
    source_first_reported_at: str | None
    source_synced_at: str
    reporting_state: str | None
    last_access_approved_at: str | None
    items: list[StationFlowApplication]
    total: int
    page: int
    page_size: int
    notice: str


class StationFlowAction(BaseModel):
    id: int
    action: str
    actor_id: int
    actor_username: str
    comment: str
    created_at: str


class StationFlowActions(BaseModel):
    station_id: int
    application_id: int
    items: list[StationFlowAction]
    total: int
    page: int
    page_size: int


def workflow_scope(db, user, station_id):
    require(db, user, "applications.read")
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "当前角色无权查看场站流转")
    station_access(db, user, station_id)
    return scoped_records(select(StationApplication), StationApplication, user).where(
        StationApplication.station_id == station_id
    )


@router.get("/{station_id}/flow", response_model=StationFlowPage)
def flow(
    station_id: int,
    user: SnapshotActor,
    db: SnapshotDB,
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(10, ge=1, le=100),
):
    stmt = workflow_scope(db, user, station_id)
    s = db.get(Station, station_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = list(
        db.scalars(
            stmt.order_by(StationApplication.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    ids = [a.id for a in rows]
    stamps = (
        {
            aid: (submitted, approved, count)
            for aid, submitted, approved, count in db.execute(
                select(
                    ApprovalAction.application_id,
                    func.max(ApprovalAction.created_at).filter(ApprovalAction.action == "submit"),
                    func.max(ApprovalAction.created_at).filter(ApprovalAction.action == "approve"),
                    func.count(),
                )
                .where(ApprovalAction.application_id.in_(ids))
                .group_by(ApprovalAction.application_id)
            )
        }
        if ids
        else {}
    )
    names = (
        dict(
            db.execute(
                select(User.id, User.username).where(User.id.in_([a.applicant_id for a in rows]))
            ).all()
        )
        if rows
        else {}
    )
    access_ids = stmt.where(StationApplication.kind == "access").with_only_columns(
        StationApplication.id
    )
    latest = db.scalar(
        select(func.max(ApprovalAction.created_at)).where(
            ApprovalAction.application_id.in_(access_ids), ApprovalAction.action == "approve"
        )
    )
    iso = lambda value: value.isoformat() if value is not None else None
    return {
        "station_id": s.id,
        "station_name": s.name,
        "platform_status": s.status,
        "source_sync_state": s.source_sync_state,
        "source_first_connected_at": iso(s.source_first_connected_at),
        "source_first_reported_at": iso(s.source_first_reported_at),
        "source_synced_at": iso(s.synced_at),
        "reporting_state": None,
        "last_access_approved_at": iso(latest),
        "items": [
            {
                "id": a.id,
                "kind": a.kind,
                "status": a.status,
                "applicant_id": a.applicant_id,
                "applicant_username": names[a.applicant_id],
                "created_at": iso(a.created_at),
                "last_submitted_at": iso(stamps.get(a.id, (None, None, 0))[0]),
                "last_approved_at": iso(stamps.get(a.id, (None, None, 0))[1]),
                "action_count": stamps.get(a.id, (None, None, 0))[2],
            }
            for a in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "notice": "仅平台实际留存的申请/动作记录；审核时间取实际approve动作，不从场站状态或更新时间推断，账号为当前账号标识。来源首次接入、首次上报和平台最近同步分别列示；正式上报时效阈值未确认，数据上报状态待确认。历史缺记录保持未知，不伪造提交或审核证明。",
    }


@router.get("/{station_id}/flow/{application_id}/actions", response_model=StationFlowActions)
def actions(
    station_id: int,
    application_id: int,
    user: SnapshotActor,
    db: SnapshotDB,
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(10, ge=1, le=100),
):
    stmt = workflow_scope(db, user, station_id)
    if not db.scalar(stmt.where(StationApplication.id == application_id)):
        raise HTTPException(404, "申请不存在或不属于当前场站")
    total = db.scalar(
        select(func.count())
        .select_from(ApprovalAction)
        .where(ApprovalAction.application_id == application_id)
    )
    rows = db.execute(
        select(ApprovalAction, User.username)
        .join(User, User.id == ApprovalAction.actor_id)
        .where(ApprovalAction.application_id == application_id)
        .order_by(ApprovalAction.created_at.desc(), ApprovalAction.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "station_id": station_id,
        "application_id": application_id,
        "items": [
            {
                "id": a.id,
                "action": a.action,
                "actor_id": a.actor_id,
                "actor_username": name,
                "comment": a.comment,
                "created_at": a.created_at.isoformat(),
            }
            for a, name in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
