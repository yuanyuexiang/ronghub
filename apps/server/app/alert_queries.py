"""Shared event filters; category comes from frozen evidence, never mutable rule names."""

from datetime import datetime
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import or_, select

from app.models import AlertEvent, Operator, Station
from app.scopes import records as scoped_records

Category = Literal[
    "station_metric",
    "device_charging",
    "device_offline",
    "device_fault",
    "energy_power",
    "unconnected_device",
    "unknown",
]


def category_expression():
    from sqlalchemy import case

    metric = AlertEvent.rule_snapshot["metric"].as_string()
    return case(
        (metric.in_(["station_utilization", "station_fault", "station_offline"]), "station_metric"),
        (metric.in_(["device_temperature", "device_current", "device_leakage"]), "device_charging"),
        (metric == "offline_minutes", "device_offline"),
        else_="unknown",
    )


def query(
    user,
    *,
    q="",
    status="",
    acknowledged=None,
    rule_id=None,
    record_id=None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    category: Category | None = None,
):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "无权访问告警")
    if status not in {"", "active", "recovered"}:
        raise HTTPException(422, "不支持的设备事件状态")
    for at in [start_at, end_at]:
        if at is not None and at.utcoffset() is None:
            raise HTTPException(422, "告警时间必须包含时区")
    if start_at and end_at and start_at >= end_at:
        raise HTTPException(422, "结束时间须晚于开始时间")
    stmt = scoped_records(
        select(AlertEvent, Station.name, Operator.name)
        .join(Station, Station.id == AlertEvent.station_id)
        .join(Operator, Operator.id == AlertEvent.operator_id),
        AlertEvent,
        user,
    )
    if user.role == "operator":
        stmt = stmt.where(AlertEvent.operator_id == user.operator_id)
    if q.strip():
        stmt = stmt.where(
            or_(
                AlertEvent.target.icontains(q.strip(), autoescape=True),
                Station.name.icontains(q.strip(), autoescape=True),
                Operator.name.icontains(q.strip(), autoescape=True),
            )
        )
    for value, column in [
        (status or None, AlertEvent.status),
        (rule_id, AlertEvent.rule_id),
        (record_id, AlertEvent.id),
    ]:
        if value is not None:
            stmt = stmt.where(column == value)
    if acknowledged is not None:
        stmt = stmt.where(
            AlertEvent.acknowledged_at.is_not(None)
            if acknowledged
            else AlertEvent.acknowledged_at.is_(None)
        )
    if start_at:
        stmt = stmt.where(AlertEvent.opened_at >= start_at)
    if end_at:
        stmt = stmt.where(AlertEvent.opened_at < end_at)
    if category:
        stmt = stmt.where(category_expression() == category)
    return stmt
