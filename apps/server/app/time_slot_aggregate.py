"""Current authorized source inventory projection, never historical availability certification."""

from fastapi import HTTPException
from sqlalchemy import distinct, func, or_, select

from app.models import ChargingOrder, HourlyEnergyRecord, Operator, Station, now
from app.order_time_slots import build_streams
from app.scopes import station_scope

NOTICE = "按当前授权且来源相同的场站台账集合汇总，北京时间开始含/结束不含；不反推历史在营范围。仅相加站级独立小时记录，不并入桩级、订单总电量或日电量。同一来源的演示标识分别列示；各流查询格按同一选中集合投影，未返回数不能跨流相加或视作营业缺失。未知与零区分，来源完整性未认证，不能推导利用率或结算。"


def inventory(user, source=None, q="", district="", operator_id=None):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "无权查看多站时段")
    stmt = station_scope(
        select(Station.id, Station.operator_id, Station.source).join(
            Operator, Operator.id == Station.operator_id
        ),
        user,
    )
    if source is not None:
        stmt = stmt.where(Station.source == source)
    if q.strip():
        stmt = stmt.where(
            or_(
                Station.name.icontains(q.strip(), autoescape=True),
                Operator.name.icontains(q.strip(), autoescape=True),
            )
        )
    if district:
        stmt = stmt.where(Station.district == district)
    if operator_id is not None:
        stmt = stmt.where(Station.operator_id == operator_id)
    return stmt


def source_options(db, user, source_q, district, operator_id, page, page_size):
    base = inventory(user, district=district, operator_id=operator_id).subquery()
    stmt = (
        select(base.c.source, func.count().label("station_count"))
        .where(base.c.source != "")
        .group_by(base.c.source)
    )
    if source_q.strip():
        stmt = stmt.where(base.c.source.icontains(source_q.strip(), autoescape=True))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.order_by(base.c.source).offset((page - 1) * page_size).limit(page_size))
    return {
        "items": [dict(row._mapping) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def report(db, user, params):
    selected = inventory(
        user, params["source"], params["q"], params["district"], params["operator_id"]
    ).subquery()
    station_count = db.scalar(select(func.count()).select_from(selected))
    start, end = params["start_at"], params["end_at"]
    order_base = (
        select(
            ChargingOrder.station_id,
            ChargingOrder.is_demo,
            ChargingOrder.started_at,
            ChargingOrder.status,
        )
        .join(
            selected,
            (selected.c.id == ChargingOrder.station_id)
            & (selected.c.operator_id == ChargingOrder.operator_id),
        )
        .where(
            ChargingOrder.source == params["source"],
            ChargingOrder.started_at >= start,
            ChargingOrder.started_at < end,
        )
        .subquery()
    )
    order_hour = func.extract("hour", func.timezone("Asia/Shanghai", order_base.c.started_at))
    order_rows = list(
        db.execute(
            select(
                order_base.c.is_demo,
                order_hour.label("hour"),
                func.count().label("observed"),
                func.count().filter(order_base.c.status == "completed").label("completed"),
            ).group_by(order_base.c.is_demo, order_hour)
        )
    )
    meter_base = (
        select(
            HourlyEnergyRecord.station_id,
            HourlyEnergyRecord.is_demo,
            HourlyEnergyRecord.started_at,
            HourlyEnergyRecord.kwh,
        )
        .join(
            selected,
            (selected.c.id == HourlyEnergyRecord.station_id)
            & (selected.c.operator_id == HourlyEnergyRecord.operator_id),
        )
        .where(
            HourlyEnergyRecord.source == params["source"],
            HourlyEnergyRecord.meter_scope == "station",
            HourlyEnergyRecord.equipment_external_id == "",
            HourlyEnergyRecord.started_at >= start,
            HourlyEnergyRecord.ended_at <= end,
        )
        .subquery()
    )
    meter_hour = func.extract("hour", func.timezone("Asia/Shanghai", meter_base.c.started_at))
    meter_rows = list(
        db.execute(
            select(
                meter_base.c.is_demo,
                meter_hour.label("hour"),
                func.count().label("reported"),
                func.count(meter_base.c.kwh).label("known"),
                func.sum(meter_base.c.kwh).label("kwh"),
            ).group_by(meter_base.c.is_demo, meter_hour)
        )
    )
    order_stations = dict(
        db.execute(
            select(order_base.c.is_demo, func.count(distinct(order_base.c.station_id))).group_by(
                order_base.c.is_demo
            )
        ).all()
    )
    meter_stations = dict(
        db.execute(
            select(meter_base.c.is_demo, func.count(distinct(meter_base.c.station_id))).group_by(
                meter_base.c.is_demo
            )
        ).all()
    )
    streams = build_streams(order_rows, meter_rows, start, end, station_count=station_count)
    for stream in streams:
        stream["stations_with_order_observations"] = order_stations.get(stream["is_demo"], 0)
        stream["stations_with_meter_records"] = meter_stations.get(stream["is_demo"], 0)
    return {
        "source": params["source"],
        "station_count": station_count,
        "q": params["q"],
        "district": params["district"],
        "operator_id": params["operator_id"],
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "streams": streams,
        "generated_at": now().isoformat(),
        "source_completeness_verified": False,
        "notice": NOTICE,
    }
