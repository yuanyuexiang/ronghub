"""A bounded combined view of independent order observations and frozen subsidy accounting."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal, localcontext
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, computed_field
from sqlalchemy import func, select

from app import order_summary, subsidy_statistics
from app.decimal_contracts import DecimalText
from app.models import Equipment, Operator, Station, SubsidyClaim
from app.scopes import station_scope

BEIJING = ZoneInfo("Asia/Shanghai")
LIMIT = 10000
NOTICE = "订单按北京时间开始时间归属周期，仅计已完成订单的已知电量/源报时长/消费，不认证来源完整或支付；来源与演示标识分开。补贴仅计完整落入周期的已批准/登记发放申请，跨周期不拆金额。设备仅列冻结系数前汇算，不分摊星级系数或人工差额；调整后及登记发放金额见场站补贴。登记发放不代表银行到账。未匹配现台账的源桩引用单列，不当作一个已确认设备。"
Period = Literal["day", "month", "year"]
Dimension = Literal["station", "equipment"]


class OrderMetric(BaseModel):
    value: DecimalText | None
    known_orders: int
    missing_orders: int


class OrderStream(BaseModel):
    source: str
    is_demo: bool
    observed_orders: int
    completed_orders: int
    kwh: OrderMetric
    duration_seconds: OrderMetric
    amount: OrderMetric

    @computed_field
    @property
    def duration_hours(self) -> DecimalText | None:
        if self.duration_seconds.value is None:
            return None
        with localcontext() as context:
            context.prec = 50
            return format(
                (Decimal(self.duration_seconds.value) / 3600).quantize(Decimal("0.000001")), "f"
            )


class SubsidyStream(BaseModel):
    is_demo: bool
    claim_count: int
    base_amount: DecimalText
    adjusted_amount: DecimalText | None
    disbursed_claim_count: int | None
    disbursed_amount: DecimalText | None


class ReportRow(BaseModel):
    key: str
    station_id: int
    station_name: str
    operator_id: int
    operator_name: str
    district: str
    kind: Literal["station", "equipment", "source_reference"]
    equipment_id: int | None = None
    equipment_external_id: str | None = None
    equipment_name: str | None = None
    reference_source: str | None = None
    orders: list[OrderStream]
    subsidies: list[SubsidyStream]


class StationSubsidy(BaseModel):
    station_id: int
    station_name: str
    operator_name: str
    streams: list[SubsidyStream]


class ChargingReport(BaseModel):
    period: Period
    period_date: date
    start_date: date
    end_date: date
    start_at: str
    end_at: str
    dimension: Dimension
    q: str
    selected_station_count: int
    current_equipment_count: int
    unmatched_order_groups: int
    cross_period_claim_count: int
    legacy_claim_count: int
    orders: list[OrderStream]
    subsidies: list[SubsidyStream]
    station_subsidies: list[StationSubsidy]
    items: list[ReportRow]
    total: int
    page: int
    page_size: int
    source_completeness_verified: Literal[False] = False
    notice: str = NOTICE


def window(period, point):
    if not 1900 <= point.year <= 9998:
        raise HTTPException(422, "报表年份须在1900至9998之间")
    first = point if period == "day" else point.replace(day=1)
    if period == "year":
        first = point.replace(month=1, day=1)
        after = date(first.year + 1, 1, 1)
    elif period == "month":
        after = date(first.year + first.month // 12, first.month % 12 + 1, 1)
    else:
        after = first + timedelta(days=1)
    return (
        first,
        after - timedelta(days=1),
        datetime.combine(first, time.min, BEIJING),
        datetime.combine(after, time.min, BEIJING),
    )


def sum_orders(values):
    groups = {}
    with localcontext() as context:
        context.prec = 50
        for value in values:
            stream = OrderStream.model_validate(value)
            key = stream.source, stream.is_demo
            if key not in groups:
                groups[key] = {
                    "source": stream.source,
                    "is_demo": stream.is_demo,
                    "observed_orders": 0,
                    "completed_orders": 0,
                    **{
                        name: {"value": None, "known_orders": 0, "missing_orders": 0}
                        for name in ["kwh", "duration_seconds", "amount"]
                    },
                }
            row = groups[key]
            row["observed_orders"] += stream.observed_orders
            row["completed_orders"] += stream.completed_orders
            for name in ["kwh", "duration_seconds", "amount"]:
                metric = getattr(stream, name)
                row[name]["known_orders"] += metric.known_orders
                row[name]["missing_orders"] += metric.missing_orders
                if metric.value is not None:
                    row[name]["value"] = format(
                        Decimal(row[name]["value"] or "0") + Decimal(metric.value), "f"
                    )
    return [OrderStream.model_validate(groups[key]) for key in sorted(groups)]


def sum_subsidies(values):
    groups = {}
    with localcontext() as context:
        context.prec = 50
        for stream in values:
            key = stream.is_demo
            if key not in groups:
                groups[key] = {
                    "is_demo": key,
                    "claim_count": 0,
                    "base_amount": "0",
                    "adjusted_amount": "0",
                    "disbursed_claim_count": 0,
                    "disbursed_amount": "0",
                }
            row = groups[key]
            row["claim_count"] += stream.claim_count
            row["disbursed_claim_count"] += stream.disbursed_claim_count or 0
            for name in ["base_amount", "adjusted_amount", "disbursed_amount"]:
                value = getattr(stream, name)
                row[name] = format(Decimal(row[name]) + Decimal(value or "0"), "f")
    return [SubsidyStream.model_validate(groups[key]) for key in sorted(groups)]


def collect(db, user, period, period_date, dimension, q, page, page_size, *, exporting=False):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "仅监管及所属企业可查看充电与补贴报表")
    first, last, start, end = window(period, period_date)
    stmt = station_scope(
        select(Station, Operator).join(Operator, Operator.id == Station.operator_id), user
    )
    if q.strip():
        stmt = stmt.where(Station.name.icontains(q.strip(), autoescape=True))
    inventory = list(db.execute(stmt.order_by(Station.id).limit(LIMIT + 1)))
    if len(inventory) > LIMIT:
        raise HTTPException(422, "报表授权场站超过10000条，请缩小范围")
    stations = {station.id: (station, operator) for station, operator in inventory}
    ids = list(stations)
    equipment = list(
        db.scalars(
            select(Equipment)
            .where(Equipment.station_id.in_(ids))
            .order_by(Equipment.id)
            .limit(LIMIT + 1)
        )
    )
    if len(equipment) > LIMIT:
        raise HTTPException(422, "报表当前设备超过10000条，请缩小范围")
    devices = {row.id: row for row in equipment}
    references = {(row.station_id, row.external_id): row.id for row in equipment}
    rows = {}

    def row(sid, key, kind, **kwargs):
        if key not in rows:
            station, operator = stations[sid]
            rows[key] = ReportRow(
                key=key,
                station_id=sid,
                station_name=station.name,
                operator_id=operator.id,
                operator_name=operator.name,
                district=station.district,
                kind=kind,
                orders=[],
                subsidies=[],
                **kwargs,
            )
        return rows[key]

    def device_row(identifier, sid):
        device = devices.get(identifier)
        if not device or device.station_id != sid:
            raise HTTPException(409, "冻结补贴设备与当前身份不一致，未生成报表")
        return row(
            sid,
            "equipment:" + str(identifier),
            "equipment",
            equipment_id=identifier,
            equipment_external_id=device.external_id,
            equipment_name=device.name,
        )

    if dimension == "station":
        for sid in ids:
            row(sid, "station:" + str(sid), "station")
    else:
        for device in equipment:
            device_row(device.id, device.station_id)
    order_params = {
        "q": "",
        "station_id": None,
        "operator_id": None,
        "equipment_external_id": "",
        "status": "",
        "source": "",
        "start_at": start,
        "end_at": end,
    }
    aggregates = list(
        db.execute(
            order_summary.ordered(
                order_summary.grouped(user, order_params, dimension, period, station_ids=ids)
            ).limit(LIMIT + 1)
        )
    )
    if len(aggregates) > LIMIT:
        raise HTTPException(422, "报表订单来源分组超过10000条，请缩小范围")
    all_orders = []
    unmatched = 0
    for aggregate in aggregates:
        value = order_summary.output(aggregate)
        stream = OrderStream.model_validate(value)
        all_orders.append(stream)
        sid = value["station_id"]
        station = stations[sid][0]
        if dimension == "station":
            target = rows["station:" + str(sid)]
        else:
            identifier = (
                references.get((sid, value["equipment_external_id"]))
                if value["source"] == station.source
                else None
            )
            if identifier is not None:
                target = device_row(identifier, sid)
            else:
                import json

                key = "source:" + json.dumps(
                    [sid, value["source"], value["equipment_external_id"]], ensure_ascii=False
                )
                target = row(
                    sid,
                    key,
                    "source_reference",
                    equipment_external_id=value["equipment_external_id"],
                    reference_source=value["source"],
                )
                unmatched += 1
        target.orders.append(stream)
    kwargs = {
        "db": db,
        "user": user,
        "start_date": first,
        "end_date": last,
        "station_ids": ids,
        "separate_demo": True,
    }
    accepted, totals = subsidy_statistics.collect(**kwargs, dimension="station", status="accepted")
    disbursed, _ = subsidy_statistics.collect(**kwargs, dimension="station", status="disbursed")
    frozen, _ = subsidy_statistics.collect(**kwargs, dimension="equipment", status="accepted")
    paid = {(value.station_id, value.is_demo): value for value in disbursed}
    subsidy_by_station = {}
    for value in accepted:
        state = paid.get((value.station_id, value.is_demo))
        stream = SubsidyStream(
            is_demo=value.is_demo,
            claim_count=value.claim_count,
            base_amount=value.base_amount,
            adjusted_amount=value.adjusted_amount,
            disbursed_claim_count=state.claim_count if state else 0,
            disbursed_amount=state.adjusted_amount if state else "0",
        )
        subsidy_by_station.setdefault(value.station_id, []).append(stream)
        if dimension == "station":
            rows["station:" + str(value.station_id)].subsidies.append(stream)
    if dimension == "equipment":
        for value in frozen:
            device_row(value.equipment_id, value.station_id).subsidies.append(
                SubsidyStream(
                    is_demo=value.is_demo,
                    claim_count=value.claim_count,
                    base_amount=value.base_amount,
                    adjusted_amount=None,
                    disbursed_claim_count=None,
                    disbursed_amount=None,
                )
            )
    crossing = db.scalar(
        station_scope(
            select(func.count())
            .select_from(SubsidyClaim)
            .join(Station, Station.id == SubsidyClaim.station_id)
            .where(
                SubsidyClaim.station_id.in_(ids),
                SubsidyClaim.operator_id == Station.operator_id,
                SubsidyClaim.status.in_(["approved", "disbursed"]),
                SubsidyClaim.start_date <= last,
                SubsidyClaim.end_date >= first,
                (SubsidyClaim.start_date < first) | (SubsidyClaim.end_date > last),
            ),
            user,
        )
    )
    if (
        len(rows) > LIMIT
        or sum(max(1, len(value.orders) + len(value.subsidies)) for value in rows.values()) > LIMIT
    ):
        raise HTTPException(422, "报表数据行超过10000条，请缩小范围；未返回部分结果")
    ordered = sorted(rows.values(), key=lambda value: (value.station_id, value.kind, value.key))
    return ChargingReport(
        period=period,
        period_date=period_date,
        start_date=first,
        end_date=last,
        start_at=start.isoformat(),
        end_at=end.isoformat(),
        dimension=dimension,
        q=q,
        selected_station_count=len(ids),
        current_equipment_count=len(equipment),
        unmatched_order_groups=unmatched,
        cross_period_claim_count=crossing,
        legacy_claim_count=totals.legacy_claim_count,
        orders=sum_orders(all_orders),
        subsidies=sum_subsidies(
            [stream for values in subsidy_by_station.values() for stream in values]
        ),
        station_subsidies=[
            StationSubsidy(
                station_id=sid,
                station_name=stations[sid][0].name,
                operator_name=stations[sid][1].name,
                streams=subsidy_by_station[sid],
            )
            for sid in sorted(subsidy_by_station)
        ],
        items=ordered if exporting else ordered[(page - 1) * page_size : page * page_size],
        total=len(ordered),
        page=page,
        page_size=page_size,
    )
