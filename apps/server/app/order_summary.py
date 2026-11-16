"""Observed order rollups: complete orders only, coverage explicit, sources never merged."""

from sqlalchemy import Date, String, cast, func, literal, select

from app.orders import statement

NOTICE = "按北京时间及订单开始时间归属日/月/年，区间开始含、结束不含，所选周期可能不完整，全单指标不按区间切分。仅汇总已完成订单的已知指标，缺失不填零；分来源及演示标识展示，不认证源完整性，不等于实付/结算或日电量、补贴。无源桩编码的行不代表一个真实电桩。"


def grouped(user, params, dimension, granularity):
    base = statement(user, params).subquery()
    c = base.c
    period = cast(func.date_trunc(granularity, func.timezone("Asia/Shanghai", c.started_at)), Date)
    equipment = c.equipment_external_id if dimension == "equipment" else literal(None, String)
    keys = [
        period.label("period_start"),
        c.station_id,
        c.station_name,
        c.operator_id,
        c.operator_name,
        c.district,
        c.source,
        equipment.label("equipment_external_id"),
        c.is_demo,
    ]
    complete = c.status == "completed"
    values = [func.count().label("observed_orders")]
    for status in ["completed", "charging", "interrupted", "cancelled", "unknown"]:
        values.append(func.count().filter(c.status == status).label(status + "_orders"))
    for name in ["kwh", "duration_seconds", "amount"]:
        values.extend(
            [
                func.sum(c[name]).filter(complete).label(name),
                func.count(c[name]).filter(complete).label(name + "_known"),
            ]
        )
    return select(*keys, *values).group_by(*keys)


def ordered(stmt):
    return stmt.order_by(
        stmt.selected_columns.period_start.desc(),
        stmt.selected_columns.station_id,
        stmt.selected_columns.source,
        stmt.selected_columns.equipment_external_id.asc().nullsfirst(),
        stmt.selected_columns.is_demo,
    )


def output(row):
    values = dict(row._mapping)
    complete = values["completed_orders"]
    for name in ["kwh", "duration_seconds", "amount"]:
        value = values[name]
        known = values.pop(name + "_known")
        values[name] = {
            "value": str(value) if value is not None else None,
            "known_orders": known,
            "missing_orders": complete - known,
        }
    values["period_start"] = values["period_start"].isoformat()
    return {**values, "currency": "CNY"}
