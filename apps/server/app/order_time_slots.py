"""Station/source hourly observations; no apportionment, availability or settlement inference."""

from datetime import UTC, timedelta
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from sqlalchemy import func, select

from app.hourly_energy import BEIJING
from app.models import ChargingOrder, HourlyEnergyRecord, Station, now
from app.scopes import station_scope

NOTICE = "按北京时间汇集00至24时，订单开始数仅表示已观测记录，含源状态，非确认充电次数；占比仅在同一来源/演示标识内计算。电量只取独立源报整小时计量，不均摊订单总电量，不混合站级与桩级。查询槽位是所选时间窗，不等于营业或可用时长；明确未知与未返回分别列示，缺失不填零，完整性未核实，不推导利用率。"


def report(db, user, params):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "无权查看订单时段数据")
    station = db.scalar(
        station_scope(select(Station), user).where(Station.id == params["station_id"])
    )
    if not station:
        raise HTTPException(404, "场站不存在或不可访问")
    start, end = params["start_at"], params["end_at"]
    order_hour = func.extract("hour", func.timezone("Asia/Shanghai", ChargingOrder.started_at))
    orders = select(
        ChargingOrder.is_demo,
        order_hour.label("hour"),
        func.count().label("observed"),
        func.count().filter(ChargingOrder.status == "completed").label("completed"),
    ).where(
        ChargingOrder.station_id == station.id,
        ChargingOrder.operator_id == station.operator_id,
        ChargingOrder.source == params["source"],
        ChargingOrder.started_at >= start,
        ChargingOrder.started_at < end,
    )
    if params["meter_scope"] == "equipment":
        orders = orders.where(
            ChargingOrder.equipment_external_id == params["equipment_external_id"]
        )
    orders = orders.group_by(ChargingOrder.is_demo, order_hour)
    meter_hour = func.extract("hour", func.timezone("Asia/Shanghai", HourlyEnergyRecord.started_at))
    meters = (
        select(
            HourlyEnergyRecord.is_demo,
            meter_hour.label("hour"),
            func.count().label("reported"),
            func.count(HourlyEnergyRecord.kwh).label("known"),
            func.sum(HourlyEnergyRecord.kwh).label("kwh"),
        )
        .where(
            HourlyEnergyRecord.station_id == station.id,
            HourlyEnergyRecord.operator_id == station.operator_id,
            HourlyEnergyRecord.source == params["source"],
            HourlyEnergyRecord.meter_scope == params["meter_scope"],
            HourlyEnergyRecord.equipment_external_id == params["equipment_external_id"],
            HourlyEnergyRecord.started_at >= start,
            HourlyEnergyRecord.ended_at <= end,
        )
        .group_by(HourlyEnergyRecord.is_demo, meter_hour)
    )
    order_rows, meter_rows = list(db.execute(orders)), list(db.execute(meters))
    streams = build_streams(order_rows, meter_rows, start, end)
    return {
        "station_id": station.id,
        "station_name": station.name,
        "source": params["source"],
        "meter_scope": params["meter_scope"],
        "equipment_external_id": params["equipment_external_id"] or None,
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "streams": streams,
        "generated_at": now().isoformat(),
        "source_completeness_verified": False,
        "notice": NOTICE,
    }


def build_streams(order_rows, meter_rows, start, end, station_count=1):
    # This denominator is a requested calendar grid, never a claim of operating hours.
    selected = [0] * 24
    stamp = start.astimezone(UTC)
    while stamp < end:
        selected[stamp.astimezone(BEIJING).hour] += 1
        stamp += timedelta(hours=1)
    streams = []
    for demo in sorted({row.is_demo for row in [*order_rows, *meter_rows]}):
        observed = {int(row.hour): row for row in order_rows if row.is_demo == demo}
        measured = {int(row.hour): row for row in meter_rows if row.is_demo == demo}
        total = sum(row.observed for row in observed.values())
        slots = []
        for hour in range(24):
            order, meter = observed.get(hour), measured.get(hour)
            count = order.observed if order else 0
            reported = meter.reported if meter else 0
            slots.append(
                {
                    "hour": hour,
                    "label": f"{hour:02d}:00–{hour + 1:02d}:00",
                    "selected_slots": selected[hour] * station_count,
                    "observed_order_starts": count,
                    "completed_order_starts": order.completed if order else 0,
                    "order_start_share_pct": str(
                        (Decimal(count) * 100 / total).quantize(
                            Decimal("0.01"), rounding=ROUND_HALF_UP
                        )
                    )
                    if total
                    else None,
                    "kwh": str(meter.kwh) if meter and meter.kwh is not None else None,
                    "known_meter_slots": meter.known if meter else 0,
                    "unknown_meter_slots": reported - meter.known if meter else 0,
                    "unreported_meter_slots": selected[hour] * station_count - reported,
                }
            )
        streams.append({"is_demo": demo, "observed_order_starts": total, "slots": slots})
    return streams
