from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app import geography
from app.dependencies import DB, Actor
from app.location import TYPES
from app.station_metadata import OPTIONS

router = APIRouter(prefix="/api/v1/map", tags=["map"])


def filters(
    q: str = Query("", max_length=150),
    district: str = Query("", max_length=50),
    operator_id: Annotated[int | None, Query(ge=1)] = None,
    station_type: str = "",
    status: str = "",
    usage_type: str = "",
    construction_site: str = "",
    site_state: str = "",
    source_sync_state: str = "",
    runtime_state: str = "",
    min_power: Annotated[Decimal | None, Query(ge=0, le=9999999999)] = None,
    max_power: Annotated[Decimal | None, Query(ge=0, le=9999999999)] = None,
    stars: int | None = Query(None, ge=0, le=5),
):
    for key, value in {
        "usage_type": usage_type,
        "construction_site": construction_site,
        "site_state": site_state,
        "source_sync_state": source_sync_state,
    }.items():
        if value and value not in OPTIONS[key]:
            raise HTTPException(422, "不支持的场站分类")
    if station_type and station_type not in TYPES:
        raise HTTPException(422, "不支持的场站类型")
    if runtime_state and runtime_state not in geography.RUNTIME:
        raise HTTPException(422, "不支持的运行快照状态")
    if status and status not in {
        "pending",
        "access_approved",
        "accepted",
        "operating",
        "decommissioned",
    }:
        raise HTTPException(422, "不支持的业务状态")
    if min_power is not None and max_power is not None and min_power > max_power:
        raise HTTPException(422, "最小功率不能大于最大功率")
    return {
        "q": q,
        "district": district,
        "operator_id": operator_id,
        "station_type": station_type,
        "status": status,
        "runtime_state": runtime_state,
        "usage_type": usage_type,
        "construction_site": construction_site,
        "site_state": site_state,
        "source_sync_state": source_sync_state,
        "min_power": min_power,
        "max_power": max_power,
        "stars": stars,
    }


def bounds(
    west: float = Query(-180, ge=-180, le=180),
    south: float = Query(-85, ge=-85, le=85),
    east: float = Query(180, ge=-180, le=180),
    north: float = Query(85, ge=-85, le=85),
):
    if west >= east or south >= north:
        raise HTTPException(422, "地图视窗范围不正确，请缩放到单一经度区间")
    return west, south, east, north


Filters = Annotated[dict, Depends(filters)]
Bounds = Annotated[tuple, Depends(bounds)]


@router.get("")
def points(
    user: Actor,
    db: DB,
    filters: Filters,
    bounds: Bounds,
    zoom: int = Query(11, ge=1, le=19),
    limit: int = Query(500, ge=1, le=500),
):
    return geography.points(db, user, filters, bounds, zoom, limit)


@router.get("/stations")
def stations(
    user: Actor,
    db: DB,
    filters: Filters,
    bounds: Bounds,
    location: str = "view",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    stmt = geography.query(db, user, filters)
    if location == "view":
        stmt = geography.in_view(stmt, bounds)
    elif location == "mapped":
        stmt = stmt.where(geography.mappable())
    elif location == "unlocated":
        stmt = stmt.where(~geography.mappable())
    elif location != "all":
        raise HTTPException(422, "不支持的位置筛选")
    total = geography.count(db, stmt)
    return {
        "items": [
            geography.output(row)
            for row in db.execute(
                stmt.order_by(geography.Station.id).offset((page - 1) * page_size).limit(page_size)
            )
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/options")
def options(user: Actor):
    return {"classifications": OPTIONS}
