"""Scoped observed-energy analysis. Coverage checks do not certify source-system completeness."""

import calendar
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select

from app.dependencies import scope
from app.models import EnergyRecord, Operator, Station, now


def station_query(user, district="", operator_id=None):
    stmt = scope(select(Station), user)
    if district:
        stmt = stmt.where(Station.district == district)
    if operator_id is not None:
        stmt = stmt.where(Station.operator_id == operator_id)
    return stmt


def shift_months(day, offset):
    year, month = divmod(day.year * 12 + day.month - 1 + offset, 12)
    if year < 1 or year > 9999:
        raise HTTPException(422, "日期超出可计算比较区间")
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def buckets(start, end, grain):
    groups = {}
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        key = (
            day.isoformat()
            if grain == "day"
            else day.strftime("%Y-%m")
            if grain == "month"
            else str(day.year)
        )
        if key not in groups:
            groups[key] = [day, day]
        groups[key][1] = day
    return groups


def report(
    db, user, start, end, district="", operator_id=None, granularity="day", dimension="station"
):
    if start > end or (end - start).days > 365 or start.year < 2:
        raise HTTPException(422, "日期范围需为1至366天，且支持前一年比较")
    if granularity not in {"day", "month", "year"} or dimension not in {
        "station",
        "district",
        "operator",
    }:
        raise HTTPException(422, "不支持的统计周期或维度")
    query_at = now()
    stations = list(db.scalars(station_query(user, district, operator_id).order_by(Station.id)))
    ids = [s.id for s in stations]
    length = (end - start).days + 1
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=length - 1)
    # One daily cube covers all current, year-over-year and period comparisons.
    earliest = min(shift_months(start, -12), previous_start)
    if granularity == "month":
        earliest = min(earliest, shift_months(start, -1))
    rows = list(
        db.execute(
            select(
                EnergyRecord.day,
                func.sum(EnergyRecord.kwh),
                func.count(),
                func.count(func.distinct(EnergyRecord.station_id)),
                func.array_agg(func.distinct(EnergyRecord.source)),
            )
            .where(
                EnergyRecord.station_id.in_(ids),
                EnergyRecord.day >= earliest,
                EnergyRecord.day <= end,
                EnergyRecord.eligible.is_(True),
            )
            .group_by(EnergyRecord.day)
            .order_by(EnergyRecord.day)
        )
    )
    today = query_at.astimezone(ZoneInfo("Asia/Shanghai")).date()

    def window(left, right):
        observed = [r for r in rows if left <= r[0] <= right]
        days = (right - left).days + 1
        station_days = sum(r[3] for r in observed)
        records = sum(r[2] for r in observed)
        sources = {source for r in observed for source in r[4]}
        return {
            "start_date": left.isoformat(),
            "end_date": right.isoformat(),
            "kwh": str(sum((r[1] for r in observed), Decimal(0))) if observed else None,
            "observed_days": len(observed),
            "requested_days": days,
            "station_days": station_days,
            "expected_station_days": len(ids) * days,
            "records": records,
            "duplicate_records": records - station_days,
            "complete_daily_observations": bool(ids)
            and station_days == len(ids) * days
            and records == station_days,
            "source_count": len(sources),
            "sources": sources,
            "ended": right < today,
        }

    def expose(value):
        return {k: v for k, v in value.items() if k != "sources"}

    def comparison(current, left, right):
        prior = window(left, right)
        delta = (
            str(Decimal(current["kwh"]) - Decimal(prior["kwh"]))
            if current["kwh"] is not None and prior["kwh"] is not None
            else None
        )
        reason = None
        if not ids:
            reason = "当前授权范围没有场站"
        elif current["kwh"] is None or prior["kwh"] is None:
            reason = "本期或比较期无观测数据"
        elif current["duplicate_records"] or prior["duplicate_records"]:
            reason = "存在同站同日多条记录，尚未确认来源分项或重叠口径"
        elif not current["complete_daily_observations"] or not prior["complete_daily_observations"]:
            reason = "同一授权场站集合存在缺失日记录"
        elif len(current["sources"]) != 1 or current["sources"] != prior["sources"]:
            reason = "两期数据来源不一致或存在多个来源"
        elif not current["ended"] or not prior["ended"]:
            reason = "比较区间尚未结束，日记录可能仍在变化"
        elif Decimal(prior["kwh"]) == 0:
            reason = "比较期电量为零，变化率无定义"
        percent = (
            None
            if reason
            else str(
                (
                    (Decimal(current["kwh"]) - Decimal(prior["kwh"])) / Decimal(prior["kwh"]) * 100
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            )
        )
        return {
            "period": expose(prior),
            "delta_kwh": delta,
            "observed_change_percent": percent,
            "unavailable_reason": reason,
            "source_completeness_verified": False,
            "basis": "仅表示同一授权场站、同一来源、完整且唯一日记录的观测电量变化；源系统完整性未认证，非结算或业务增长率",
        }

    def comparisons(current, left, right):
        prior_left, prior_right = (
            (left - timedelta(days=(right - left).days + 1), left - timedelta(days=1))
            if granularity == "day"
            else (
                shift_months(left, -1 if granularity == "month" else -12),
                shift_months(right, -1 if granularity == "month" else -12),
            )
        )
        return {
            "year_over_year": comparison(
                current, shift_months(left, -12), shift_months(right, -12)
            ),
            "period_over_period": comparison(current, prior_left, prior_right),
        }

    current = window(start, end)
    timeline = []
    for key, (left, right) in buckets(start, end, granularity).items():
        value = window(left, right)
        timeline.append(
            {"period": key, **expose(value), "comparisons": comparisons(value, left, right)}
        )
    daily = {r[0]: r for r in rows if start <= r[0] <= end}
    totals = dict(
        db.execute(
            select(EnergyRecord.station_id, func.sum(EnergyRecord.kwh))
            .where(
                EnergyRecord.station_id.in_(ids),
                EnergyRecord.day >= start,
                EnergyRecord.day <= end,
                EnergyRecord.eligible.is_(True),
            )
            .group_by(EnergyRecord.station_id)
        ).all()
    )
    operators = {
        o.id: o.name
        for o in db.scalars(
            select(Operator).where(Operator.id.in_({s.operator_id for s in stations}))
        )
    }
    ranking = [
        {
            "id": s.id,
            "name": s.name,
            "district": s.district,
            "operator": operators.get(s.operator_id, ""),
            "operator_id": s.operator_id,
            "status": s.status,
            "connectors": s.connectors,
            "power_kw": str(s.power_kw),
            "kwh": str(totals[s.id]) if s.id in totals else None,
        }
        for s in stations
    ]
    ranking.sort(
        key=lambda r: Decimal(r["kwh"]) if r["kwh"] is not None else Decimal(-1), reverse=True
    )
    grouped = {}
    for s in ranking:
        key = (
            str(s["id"])
            if dimension == "station"
            else s["district"]
            if dimension == "district"
            else str(s["operator_id"])
        )
        if key not in grouped:
            grouped[key] = {
                "key": key,
                "name": s["name"]
                if dimension == "station"
                else (s["district"] or "区域未提供")
                if dimension == "district"
                else s["operator"],
                "station_count": 0,
                "connectors": 0,
                "power_kw": Decimal(0),
                "kwh": None,
            }
        group = grouped[key]
        group["station_count"] += 1
        group["connectors"] += s["connectors"]
        group["power_kw"] += Decimal(s["power_kw"])
        if s["kwh"] is not None:
            group["kwh"] = (group["kwh"] or Decimal(0)) + Decimal(s["kwh"])
    dimension_ranking = sorted(
        grouped.values(),
        key=lambda r: r["kwh"] if r["kwh"] is not None else Decimal(-1),
        reverse=True,
    )
    for group in dimension_ranking:
        group["power_kw"] = str(group["power_kw"])
        group["kwh"] = str(group["kwh"]) if group["kwh"] is not None else None
    distribution = {}
    for station in stations:
        distribution[station.status] = distribution.get(station.status, 0) + 1
    return {
        "generated_at": query_at.isoformat(),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "timezone": "Asia/Shanghai",
        "energy_date_basis": "来源日记录的业务日期，真实接口时区映射待确认",
        "granularity": granularity,
        "dimension": dimension,
        "comparison_date_basis": "同一授权场站集合；同比前移12个月，月/年环比分别前移1/12个月；日环比前一等长区间。月底与闰日截到目标月末，部分周期按所选日期截断。",
        "station_count": len(stations),
        "connectors": sum(s.connectors for s in stations),
        "power_kw": str(sum((s.power_kw for s in stations), Decimal(0))),
        "total_kwh": current["kwh"],
        "observed_days": current["observed_days"],
        "requested_days": length,
        "coverage": expose(current),
        "comparisons": comparisons(current, start, end),
        "previous_period": expose(window(previous_start, previous_end)),
        "timeline": timeline,
        "daily": [
            {
                "day": (day := start + timedelta(days=offset)).isoformat(),
                "kwh": str(daily[day][1]) if day in daily else None,
                "records": daily[day][2] if day in daily else 0,
                "stations": daily[day][3] if day in daily else 0,
            }
            for offset in range(length)
        ],
        "ranking": ranking,
        "dimension_ranking": dimension_ranking,
        "status_distribution": distribution,
        "unavailable": {
            "utilization": "缺少充电持续时间及可用容量口径",
            "order_count": "本电量报表不合并独立订单来源，请查看充电订单或订单汇总",
            "charging_hours": "日级电量无法还原充电时段",
            "online_rate": "缺少设备状态历史与统计分母",
        },
        "basis": "仅汇总当前授权场站的有效电量记录；空值无观测不等于零，台账规模为当前快照而非历史建站量；观测变化不是已认证业务增长，当前为演示数据、非结算报表",
    }
