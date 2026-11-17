"""Scoped station map with bounded geographic-grid aggregation and honest unknown states."""

from fastapi import HTTPException
from sqlalchemy import and_, case, func, select

from app.authorization import permissions
from app.models import Connector, Equipment, Operator, RatingApplication, Station, now
from app.rating_results import local_day
from app.scopes import station_scope

RUNTIME = {"available", "charging", "occupied", "offline", "fault", "unknown"}


def mappable():
    return and_(
        Station.coordinate_system == "wgs84",
        Station.longitude.is_not(None),
        Station.latitude.between(-85, 85),
    )


def query(db, user, filters):
    counts = (
        select(
            Equipment.station_id.label("station_id"),
            func.count(Connector.id).label("observed"),
            *[
                func.sum(case((Connector.status == state, 1), else_=0)).label(state)
                for state in RUNTIME - {"unknown"}
            ],
        )
        .join(Connector, Connector.equipment_id == Equipment.id)
        .where(Equipment.station_id.in_(station_scope(select(Station.id), user)))
        .group_by(Equipment.station_id)
        .subquery()
    )
    observed = func.coalesce(counts.c.observed, 0)
    runtime = case(
        (counts.c.fault > 0, "fault"),
        (counts.c.charging > 0, "charging"),
        (counts.c.occupied > 0, "occupied"),
        (counts.c.available > 0, "available"),
        (
            and_(observed > 0, observed >= Station.connectors, counts.c.offline == observed),
            "offline",
        ),
        else_="unknown",
    )
    stmt = (
        station_scope(
            select(
                Station,
                Operator.name.label("operator_name"),
                runtime.label("runtime_state"),
                observed.label("observed_connectors"),
            ),
            user,
        )
        .join(Operator, Operator.id == Station.operator_id)
        .outerjoin(counts, counts.c.station_id == Station.id)
    )
    can_rating = "ratings.read" in permissions(db, user)
    if filters.get("stars") is not None and not can_rating:
        raise HTTPException(403, "当前账号无权按星级筛选")
    if can_rating:
        ranked = (
            select(
                RatingApplication.id,
                func.row_number()
                .over(
                    partition_by=RatingApplication.station_id,
                    order_by=(
                        func.coalesce(
                            RatingApplication.published_at, RatingApplication.created_at
                        ).desc(),
                        RatingApplication.id.desc(),
                    ),
                )
                .label("position"),
            )
            .where(
                RatingApplication.status.in_(["published", "delisted"]),
                RatingApplication.station_id.in_(station_scope(select(Station.id), user)),
            )
            .subquery()
        )
        rating = (
            select(
                RatingApplication.station_id,
                RatingApplication.status,
                RatingApplication.valid_until,
                RatingApplication.calculation,
            )
            .join(ranked, ranked.c.id == RatingApplication.id)
            .where(ranked.c.position == 1)
            .subquery()
        )
        stars = case(
            (rating.c.station_id.is_(None), None),
            (rating.c.status == "delisted", 0),
            (~Station.status.in_(["access_approved", "accepted", "operating"]), 0),
            (rating.c.valid_until.is_(None), None),
            (rating.c.valid_until < local_day(), 0),
            else_=rating.c.calculation["stars"].as_integer(),
        )
        stmt = stmt.outerjoin(rating, rating.c.station_id == Station.id)
    else:
        stars = case((Station.id.is_(None), None), else_=None)
    stmt = stmt.add_columns(stars.label("effective_stars"))
    if filters.get("q"):
        literal = filters["q"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(Station.name.ilike("%" + literal + "%", escape="\\"))
    for key in (
        "district",
        "operator_id",
        "station_type",
        "status",
        "usage_type",
        "construction_site",
        "site_state",
        "source_sync_state",
    ):
        if filters.get(key):
            stmt = stmt.where(getattr(Station, key) == filters[key])
    if filters.get("runtime_state"):
        stmt = stmt.where(runtime == filters["runtime_state"])
    if filters.get("min_power") is not None:
        stmt = stmt.where(Station.power_kw >= filters["min_power"])
    if filters.get("max_power") is not None:
        stmt = stmt.where(Station.power_kw <= filters["max_power"])
    if filters.get("stars") is not None:
        stmt = stmt.where(stars == filters["stars"])
    return stmt


def in_view(stmt, bounds):
    west, south, east, north = bounds
    return stmt.where(
        mappable(), Station.longitude.between(west, east), Station.latitude.between(south, north)
    )


def count(db, stmt):
    return db.scalar(
        select(func.count()).select_from(stmt.with_only_columns(Station.id).subquery())
    )


def list_query(db, user, filters, bounds, location):
    stmt = query(db, user, filters)
    if location == "view":
        return in_view(stmt, bounds)
    if location == "mapped":
        return stmt.where(mappable())
    if location == "unlocated":
        return stmt.where(~mappable())
    if location == "all":
        return stmt
    raise HTTPException(422, "不支持的位置筛选")


def output(row):
    station = row[0]
    if station.longitude is None:
        reason = "missing_coordinates"
    elif station.coordinate_system != "wgs84":
        reason = "unsupported_coordinate_system"
    elif not -85 <= station.latitude <= 85:
        reason = "outside_projection"
    else:
        reason = None
    return {
        "id": station.id,
        "name": station.name,
        "district": station.district,
        "operator_id": station.operator_id,
        "operator_name": row.operator_name,
        "address": station.address,
        "status": station.status,
        "station_type": station.station_type,
        "usage_type": station.usage_type,
        "construction_site": station.construction_site,
        "site_state": station.site_state,
        "source_sync_state": station.source_sync_state,
        "source_first_connected_at": station.source_first_connected_at.isoformat()
        if station.source_first_connected_at
        else None,
        "power_kw": str(station.power_kw),
        "connectors": station.connectors,
        "observed_connectors": row.observed_connectors,
        "missing_connector_snapshots": max(0, station.connectors - row.observed_connectors),
        "runtime_state": row.runtime_state,
        "effective_stars": row.effective_stars,
        "longitude": str(station.longitude) if station.longitude is not None else None,
        "latitude": str(station.latitude) if station.latitude is not None else None,
        "coordinate_system": station.coordinate_system,
        "location_is_demo": station.location_is_demo,
        "unplottable_reason": reason,
        "synced_at": station.synced_at.isoformat(),
    }


def points(db, user, filters, bounds, zoom, limit):
    base = query(db, user, filters)
    view = in_view(base, bounds)
    coords = view.with_only_columns(Station.id, Station.longitude, Station.latitude).subquery()
    # Geographic cells, explicitly not pixel-distance or administrative boundaries.
    lon_step, lat_step = 90 / 2**zoom, 45 / 2**zoom
    x, y = (
        func.floor((coords.c.longitude + 180) / lon_step),
        func.floor((coords.c.latitude + 90) / lat_step),
    )
    groups = (
        select(
            x.label("x"),
            y.label("y"),
            func.count().label("count"),
            func.min(coords.c.id).label("single_id"),
            func.avg(coords.c.longitude).label("longitude"),
            func.avg(coords.c.latitude).label("latitude"),
            func.min(coords.c.longitude).label("west"),
            func.max(coords.c.longitude).label("east"),
            func.min(coords.c.latitude).label("south"),
            func.max(coords.c.latitude).label("north"),
        )
        .group_by(x, y)
        .subquery()
    )
    totals = db.execute(select(func.count(), func.coalesce(func.sum(groups.c.count), 0))).one()
    cells = list(db.execute(select(groups).order_by(groups.c.x, groups.c.y).limit(limit)))
    ids = [cell.single_id for cell in cells if cell.count == 1]
    singles = {row[0].id: output(row) for row in db.execute(base.where(Station.id.in_(ids)))}
    items = []
    for cell in cells:
        if cell.count == 1:
            items.append({"kind": "station", **singles[cell.single_id]})
        else:
            items.append(
                {
                    "kind": "cluster",
                    "key": f"{zoom}:{cell.x}:{cell.y}",
                    "count": cell.count,
                    "longitude": str(cell.longitude),
                    "latitude": str(cell.latitude),
                    "bounds": [
                        float(cell.west),
                        float(cell.south),
                        float(cell.east),
                        float(cell.north),
                    ],
                }
            )
    matching = count(db, base)
    mapped = count(db, base.where(mappable()))
    return {
        "items": items,
        "matching": matching,
        "mappable": mapped,
        "unplottable": matching - mapped,
        "in_view": int(totals[1]),
        "cell_count": totals[0],
        "truncated": totals[0] > limit,
        "zoom": zoom,
        "coordinate_system": "wgs84",
        "generated_at": now().isoformat(),
        "aggregation": "固定经纬度网格，非行政区域；放大查看单站或使用分页列表",
        "runtime_basis": "最近同步枪口状态快照，非实时监控；缺少快照不推断离线",
    }
