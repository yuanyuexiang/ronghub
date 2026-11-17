"""Current inventory and independent period observations for the Web workbench."""

from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import Date, DateTime, cast, func, select, tuple_

from app.authorization import permissions
from app.charging_report import OrderStream, Period, window
from app.decimal_contracts import DecimalText
from app.models import EnergyRecord, Equipment, Registration, Station, StationApplication
from app.orders import statement
from app.scopes import station_scope


class StockGroup(BaseModel):
    key: str
    count: int
    share_pct: DecimalText | None


class PeriodEnergy(BaseModel):
    source: str
    kwh: DecimalText
    records: int
    observed_station_days: int
    duplicate_station_days: int


class MonthObservation(BaseModel):
    month: date
    kwh: DecimalText | None
    records: int


class TrendStream(BaseModel):
    source: str
    months: list[MonthObservation]


class ApprovalCounts(BaseModel):
    approved: int
    rejected: int
    submitted: int
    other: int
    approval_rate_pct: None = None
    notice: str = (
        "当前接入申请状态计数，不是去重场站通过率；正式审核通过率的分母及退回重报口径待确认。"
    )


class OperationsDashboard(BaseModel):
    period: Period
    period_date: date
    start_date: date
    end_date: date
    start_at: str
    end_at: str
    current_stations: int
    current_equipment: int
    current_equipment_power_kw: DecimalText
    operators_with_stations: int
    approved_company_records: int | None
    statuses: list[StockGroup]
    station_types: list[StockGroup]
    districts: list[StockGroup]
    approvals: ApprovalCounts | None
    orders: list[OrderStream] | None
    energy: list[PeriodEnergy] | None
    trend_start_date: date
    trend_end_date: date
    energy_trend: list[TrendStream] | None
    source_completeness_verified: Literal[False] = False
    notice: str = "场站分布为当前授权台账状态，不反推历史库存或累计接入历史。订单按北京开始时间归属且仅汇总已完成的已知指标；日电量按业务日分来源独立统计，不互相补造，缺观测不填零。最近12个月截至所选日期，首月从月初起，末月可能未结束。企业审核计数只含留存的通过记录，不把旧台账当审核证明。"


def report(db, user, period, point):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "协会工作台不开放运营业务观测")
    first, last, start, end = window(period, point)
    stock = station_scope(
        select(
            Station.id, Station.operator_id, Station.status, Station.station_type, Station.district
        ),
        user,
    ).subquery()
    total = db.scalar(select(func.count()).select_from(stock))
    granted = permissions(db, user)

    def groups(column):
        values = db.execute(select(column, func.count()).group_by(column).order_by(column))
        return [
            StockGroup(
                key=key,
                count=count,
                share_pct=format((Decimal(count) * 100 / total).quantize(Decimal("0.01")), "f")
                if total
                else None,
            )
            for key, count in values
        ]

    approvals = None
    if "applications.read" in granted:
        counts = dict(
            db.execute(
                select(StationApplication.status, func.count())
                .where(
                    StationApplication.station_id.in_(select(stock.c.id)),
                    StationApplication.kind == "access",
                )
                .group_by(StationApplication.status)
            ).all()
        )
        approvals = ApprovalCounts(
            approved=counts.pop("approved", 0),
            rejected=counts.pop("rejected", 0),
            submitted=counts.pop("submitted", 0),
            other=sum(counts.values()),
        )
    streams = None
    if "orders.read" in granted:
        params = {
            "q": "",
            "station_id": None,
            "operator_id": None,
            "equipment_external_id": "",
            "status": "",
            "source": "",
            "start_at": start,
            "end_at": end,
        }
        base = statement(user, params).subquery()
        complete = base.c.status == "completed"
        values = [
            base.c.source,
            base.c.is_demo,
            func.count().label("observed_orders"),
            func.count().filter(complete).label("completed_orders"),
        ]
        for name in ["kwh", "duration_seconds", "amount"]:
            values.extend(
                [
                    func.sum(base.c[name]).filter(complete).label(name),
                    func.count(base.c[name]).filter(complete).label(name + "_known"),
                ]
            )
        streams = []
        for row in db.execute(
            select(*values)
            .group_by(base.c.source, base.c.is_demo)
            .order_by(base.c.source, base.c.is_demo)
        ):
            data = dict(row._mapping)
            for name in ["kwh", "duration_seconds", "amount"]:
                value = data[name]
                known = data.pop(name + "_known")
                data[name] = {
                    "value": str(value) if value is not None else None,
                    "known_orders": known,
                    "missing_orders": data["completed_orders"] - known,
                }
            streams.append(OrderStream.model_validate(data))
    month_index = point.year * 12 + point.month - 1 - 11
    trend_first = date(month_index // 12, month_index % 12 + 1, 1)
    months = [date((month_index + i) // 12, (month_index + i) % 12 + 1, 1) for i in range(12)]
    energy = trend = None
    if "statistics.read" in granted:

        def observations(begin, finish):
            return (
                select(EnergyRecord)
                .where(
                    EnergyRecord.station_id.in_(select(stock.c.id)),
                    EnergyRecord.eligible.is_(True),
                    EnergyRecord.day >= begin,
                    EnergyRecord.day <= finish,
                )
                .subquery()
            )

        selected = observations(first, last)
        energy = []
        for row in db.execute(
            select(
                selected.c.source,
                func.sum(selected.c.kwh).label("kwh"),
                func.count().label("records"),
                func.count(func.distinct(tuple_(selected.c.station_id, selected.c.day))).label(
                    "days"
                ),
            )
            .group_by(selected.c.source)
            .order_by(selected.c.source)
        ):
            energy.append(
                PeriodEnergy(
                    source=row.source,
                    kwh=str(row.kwh),
                    records=row.records,
                    observed_station_days=row.days,
                    duplicate_station_days=row.records - row.days,
                )
            )
        historical = observations(trend_first, point)
        bucket = cast(func.date_trunc("month", cast(historical.c.day, DateTime)), Date)
        sources = {}
        for row in db.execute(
            select(
                historical.c.source,
                bucket.label("month"),
                func.sum(historical.c.kwh).label("kwh"),
                func.count().label("records"),
            )
            .group_by(historical.c.source, bucket)
            .order_by(historical.c.source, bucket)
        ):
            sources.setdefault(row.source, {})[row.month] = row
        trend = [
            TrendStream(
                source=source,
                months=[
                    MonthObservation(
                        month=month,
                        kwh=str(values[month].kwh) if month in values else None,
                        records=values[month].records if month in values else 0,
                    )
                    for month in months
                ],
            )
            for source, values in sources.items()
        ]
    equipment_stock = db.execute(
        select(func.count(Equipment.id), func.coalesce(func.sum(Equipment.power_kw), 0)).where(
            Equipment.station_id.in_(select(stock.c.id))
        )
    ).one()
    return OperationsDashboard(
        period=period,
        period_date=point,
        start_date=first,
        end_date=last,
        start_at=start.isoformat(),
        end_at=end.isoformat(),
        current_stations=total,
        current_equipment=equipment_stock[0],
        current_equipment_power_kw=str(equipment_stock[1]),
        operators_with_stations=db.scalar(select(func.count(func.distinct(stock.c.operator_id)))),
        approved_company_records=db.scalar(
            select(func.count(func.distinct(Registration.operator_id))).where(
                Registration.operator_id.in_(select(stock.c.operator_id)),
                Registration.status == "approved",
            )
        )
        if "companies.read" in granted
        else None,
        statuses=groups(stock.c.status),
        station_types=groups(stock.c.station_type),
        districts=groups(stock.c.district),
        approvals=approvals,
        orders=streams,
        energy=energy,
        trend_start_date=trend_first,
        trend_end_date=point,
        energy_trend=trend,
    )
