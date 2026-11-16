from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.authorization import permissions
from app.config import settings
from app.contracts import DashboardSummary, EquipmentList, OperatorList, StationDetail, StationPage
from app.dependencies import DB, Actor, scope
from app.models import Station, StationApplication
from app.scopes import company_scope

router = APIRouter()


@router.get("/api/v1/stations", response_model=StationPage)
def stations(
    user: Actor,
    db: DB,
    q: str = Query("", max_length=150),
    status: str = Query("", max_length=40),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    district: str = Query("", max_length=50),
    operator_id: int | None = None,
):
    stmt = scope(select(Station), user)
    if q:
        stmt = stmt.where(Station.name.ilike("%" + q + "%"))
    if status:
        stmt = stmt.where(Station.status.in_(status.split(",")))
    if district:
        stmt = stmt.where(Station.district == district)
    if operator_id is not None:
        stmt = stmt.where(Station.operator_id == operator_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Station.id).offset((page - 1) * page_size).limit(page_size))
    return {
        "items": [
            {
                "id": s.id,
                "name": s.name,
                "district": s.district,
                "address": s.address,
                "status": s.status,
                "operator_id": s.operator_id,
                "connectors": s.connectors,
                "power_kw": str(s.power_kw),
                "version": s.version,
                "synced_at": s.synced_at.isoformat(),
            }
            for s in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/api/v1/stations/{station_id}", response_model=StationDetail)
def station(station_id: int, user: Actor, db: DB):
    s = db.scalar(scope(select(Station).where(Station.id == station_id), user))
    if not s:
        raise HTTPException(404, "场站不存在或不可访问")
    return {
        "source": s.source,
        "external_id": s.external_id,
        "id": s.id,
        "name": s.name,
        "status": s.status,
        "version": s.version,
        "address": s.address,
        "power_kw": str(s.power_kw),
        "district": s.district,
        "usage_type": s.usage_type,
        "construction_site": s.construction_site,
        "site_state": s.site_state,
        "source_sync_state": s.source_sync_state,
        "source_first_connected_at": s.source_first_connected_at.isoformat()
        if s.source_first_connected_at
        else None,
        "operator_id": s.operator_id,
        "station_type": s.station_type,
        "longitude": str(s.longitude) if s.longitude is not None else None,
        "latitude": str(s.latitude) if s.latitude is not None else None,
        "coordinate_system": s.coordinate_system,
        "location_is_demo": s.location_is_demo,
        "synced_at": s.synced_at.isoformat(),
    }


@router.get("/api/v1/dashboard", response_model=DashboardSummary, response_model_exclude_unset=True)
def dashboard(user: Actor, db: DB):
    rows = list(db.scalars(scope(select(Station), user)))
    from app.dashboard import summary

    return {
        **summary(db, user, [s.id for s in rows]),
        "stations": len(rows),
        "connectors": sum(s.connectors for s in rows),
        "power_kw": str(sum((s.power_kw for s in rows), Decimal(0))),
        "pending": None
        if user.role == "association" or "applications.read" not in permissions(db, user)
        else db.scalar(
            select(func.count())
            .select_from(StationApplication)
            .where(
                StationApplication.station_id.in_([s.id for s in rows]),
                StationApplication.kind == "access",
                StationApplication.status == "submitted",
            )
        ),
        "demo_mode": settings.demo_mode,
    }


@router.get("/api/v1/stations/{station_id}/equipment", response_model=EquipmentList)
def station_equipment(station_id: int, user: Actor, db: DB):
    from app.models import Connector, Equipment

    if not db.scalar(scope(select(Station).where(Station.id == station_id), user)):
        raise HTTPException(404, "场站不存在或不可访问")
    return {
        "items": [
            {
                "id": e.id,
                "name": e.name,
                "power_kw": str(e.power_kw),
                "connectors": [
                    {"id": c.id, "external_id": c.external_id, "status": c.status}
                    for c in db.scalars(select(Connector).where(Connector.equipment_id == e.id))
                ],
            }
            for e in db.scalars(select(Equipment).where(Equipment.station_id == station_id))
        ]
    }


@router.get("/api/v1/operators", response_model=OperatorList)
def operators(user: Actor, db: DB):
    from app.models import Operator

    stmt = company_scope(select(Operator), user)
    if user.role == "operator":
        stmt = stmt.where(Operator.id == user.operator_id)
    elif user.role not in {"regulator", "association"}:
        raise HTTPException(403, "无权访问")
    return {"items": [{"id": o.id, "name": o.name} for o in db.scalars(stmt.order_by(Operator.id))]}
