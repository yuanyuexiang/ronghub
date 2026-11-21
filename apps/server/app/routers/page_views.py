"""Scoped read models for customer prototype workspaces; never invent missing source metrics."""

from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select

from app.authorization import require
from app.dependencies import SnapshotActor
from app.models import Connector, EnergyRecord, Equipment, Operator, Station
from app.reporting import SnapshotDB
from app.scopes import station_scope

router = APIRouter(prefix="/api/v1/page-views", tags=["workspaces"])


class MonitorRow(BaseModel):
    id: int
    station_id: int
    station_name: str
    operator_name: str
    district: str
    equipment_name: str
    equipment_code: str
    connector_code: str | None
    state: str
    source: str
    synced_at: str


class MonitorPage(BaseModel):
    items: list[MonitorRow]
    total: int
    page: int
    page_size: int
    equipment_count: int
    station_count: int
    states: dict[str, int]
    station_types: dict[str, int]
    electrical_count: None = None
    environmental_count: None = None


@router.get("/monitoring", response_model=MonitorPage)
def monitoring(
    user: SnapshotActor,
    db: SnapshotDB,
    q: str = Query("", max_length=150),
    district: str = Query("", max_length=50),
    element: Literal["operating", "electrical", "environmental"] = "operating",
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    require(db, user, "stations.read")
    scoped = station_scope(select(Station).where(Station.name.icontains(q, autoescape=True)), user)
    if district:
        scoped = scoped.where(Station.district == district)
    ids = scoped.with_only_columns(Station.id)
    station_types = dict(
        db.execute(
            scoped.with_only_columns(Station.station_type, func.count()).group_by(
                Station.station_type
            )
        ).all()
    )
    equipment_count = db.scalar(
        select(func.count()).select_from(Equipment).where(Equipment.station_id.in_(ids))
    )
    station_count = db.scalar(select(func.count()).select_from(scoped.subquery()))
    stmt = (
        select(Connector, Equipment, Station, Operator.name)
        .select_from(Equipment)
        .outerjoin(Connector, Connector.equipment_id == Equipment.id)
        .join(Station, Station.id == Equipment.station_id)
        .join(Operator, Operator.id == Station.operator_id)
        .where(Equipment.station_id.in_(ids))
    )
    counts = db.execute(
        select(Connector.status, func.count())
        .select_from(Connector)
        .join(Equipment, Equipment.id == Connector.equipment_id)
        .where(Equipment.station_id.in_(ids))
        .group_by(Connector.status)
    )
    states = {state: count for state, count in counts}
    if element != "operating":
        return {
            "items": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "equipment_count": equipment_count,
            "station_count": station_count,
            "states": states,
            "station_types": station_types,
        }
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(
        stmt.order_by(Station.id, Equipment.id, Connector.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [
            {
                "id": connector.id if connector else -equipment.id,
                "station_id": station.id,
                "station_name": station.name,
                "operator_name": name,
                "district": station.district,
                "equipment_name": equipment.name,
                "equipment_code": equipment.external_id,
                "connector_code": connector.external_id if connector else None,
                "state": connector.status if connector else "unknown",
                "source": station.source,
                "synced_at": station.synced_at.isoformat(),
            }
            for connector, equipment, station, name in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "equipment_count": equipment_count,
        "station_count": station_count,
        "states": states,
        "station_types": station_types,
    }


class RankingRow(BaseModel):
    station_id: int
    station_name: str
    source: str
    yesterday_kwh: str | None
    today_kwh: str | None


class RankingPage(BaseModel):
    items: list[RankingRow]
    today: str
    yesterday: str
    total: int
    page: int
    page_size: int


@router.get("/station-ranking", response_model=RankingPage)
def ranking(
    user: SnapshotActor,
    db: SnapshotDB,
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(5, ge=1, le=100),
):
    require(db, user, "statistics.read")
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    yesterday = today - timedelta(days=1)
    ids = station_scope(select(Station.id), user)
    prior = func.sum(case((EnergyRecord.day == yesterday, EnergyRecord.kwh), else_=None))
    current = func.sum(case((EnergyRecord.day == today, EnergyRecord.kwh), else_=None))
    stmt = (
        select(
            Station.id,
            Station.name,
            EnergyRecord.source,
            prior.label("prior"),
            current.label("current"),
        )
        .join(EnergyRecord, EnergyRecord.station_id == Station.id)
        .where(
            Station.id.in_(ids),
            EnergyRecord.eligible.is_(True),
            EnergyRecord.day.in_([today, yesterday]),
        )
        .group_by(Station.id, Station.name, EnergyRecord.source)
    )
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(
        stmt.order_by(prior.desc().nulls_last(), Station.id, EnergyRecord.source)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [
            {
                "station_id": sid,
                "station_name": name,
                "source": source,
                "yesterday_kwh": str(a) if a is not None else None,
                "today_kwh": str(b) if b is not None else None,
            }
            for sid, name, source, a, b in rows
        ],
        "today": today.isoformat(),
        "yesterday": yesterday.isoformat(),
        "total": total,
        "page": page,
        "page_size": page_size,
    }


class DailyEnergyRow(BaseModel):
    day: str
    source: str
    kwh: str
    subsidy_amount: None = None


class DailyEnergyPage(BaseModel):
    items: list[DailyEnergyRow]
    total: int
    page: int
    page_size: int


@router.get("/daily-energy", response_model=DailyEnergyPage)
def daily_energy(
    user: SnapshotActor,
    db: SnapshotDB,
    start_date: date,
    end_date: date,
    source: str = Query("", max_length=50),
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    require(db, user, "subsidy.read")
    if start_date > end_date or (end_date - start_date).days > 365:
        raise HTTPException(422, "日期范围须有序且最多366天")
    ids = station_scope(select(Station.id), user)
    stmt = (
        select(EnergyRecord.day, EnergyRecord.source, func.sum(EnergyRecord.kwh))
        .where(
            EnergyRecord.station_id.in_(ids),
            EnergyRecord.eligible.is_(True),
            EnergyRecord.day.between(start_date, end_date),
        )
        .group_by(EnergyRecord.day, EnergyRecord.source)
    )
    if source:
        stmt = stmt.where(EnergyRecord.source == source)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(
        stmt.order_by(EnergyRecord.day, EnergyRecord.source)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [{"day": day.isoformat(), "source": s, "kwh": str(kwh)} for day, s, kwh in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def csv_response(db, user, result, keys, titles, capability, name):
    import csv
    import io

    from fastapi.responses import Response

    from app.models import Audit
    from app.routers.statistics import csv_cell

    require(db, user, capability)
    if result["total"] > 10000:
        raise HTTPException(422, "结果超过10000条，请缩小范围；未截断导出")
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(titles)
    for item in result["items"]:
        writer.writerow([csv_cell(item.get(k)) for k in keys])
    db.add(Audit(user_id=user.id, action=f"{name}.export", detail={"rows": result["total"]}))
    db.commit()
    return Response(
        content=("\ufeff" + stream.getvalue()).encode(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="ronghub-{name}.csv"',
            "Cache-Control": "private, no-store",
        },
    )


from fastapi.responses import Response

CSV = {200: {"content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}}}}


@router.get("/monitoring/export", response_class=Response, responses=CSV)
def monitor_export(
    user: SnapshotActor,
    db: SnapshotDB,
    q: str = Query("", max_length=150),
    district: str = Query("", max_length=50),
    element: Literal["operating", "electrical", "environmental"] = "operating",
):
    require(db, user, "stations.export")
    data = monitoring(user, db, q, district, element, 1, 10001)
    return csv_response(
        db,
        user,
        data,
        [
            "station_name",
            "operator_name",
            "district",
            "equipment_name",
            "connector_code",
            "state",
            "source",
            "synced_at",
        ],
        ["场站", "运营商", "区域", "设备", "枪口编码", "来源状态", "来源", "台账同步时间"],
        "stations.export",
        "monitoring",
    )


@router.get("/daily-energy/export", response_class=Response, responses=CSV)
def daily_export(
    user: SnapshotActor,
    db: SnapshotDB,
    start_date: date,
    end_date: date,
    source: str = Query("", max_length=50),
):
    require(db, user, "subsidy.export")
    data = daily_energy(user, db, start_date, end_date, source, 1, 10001)
    return csv_response(
        db,
        user,
        data,
        ["day", "source", "kwh", "subsidy_amount"],
        ["日期", "独立来源", "电量汇总(度)", "按日生成补贴(未留存)"],
        "subsidy.export",
        "daily-energy",
    )
