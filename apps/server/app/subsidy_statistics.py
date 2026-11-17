"""Bounded, snapshot-consistent application statistics; never prorate financial claims."""

from decimal import Decimal, localcontext
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from app.decimal_contracts import DecimalText
from app.models import Operator, Station, SubsidyClaim, SubsidyDeviceLine
from app.scopes import station_scope
from app.subsidy_execution_contracts import DeviceClaimCalculation, DeviceLineCalculation

CLAIM_LIMIT = 10000
LINE_LIMIT = 50000
STATUSES = {
    "accepted": ["approved", "disbursed"],
    "approved": ["approved"],
    "disbursed": ["disbursed"],
    "submitted": ["submitted"],
    "draft": ["draft"],
}
NOTICE = "演示申请统计；仅计完整落入查询范围的申请周期，不按天拆分金额。名称/信用代码取当前台账；设备名称及电量取冻结明细。设备金额为系数前汇算，不分摊星级系数及人工差额；非正式结算。"
Dimension = Literal["operator", "station", "equipment", "device_day"]
Status = Literal["accepted", "approved", "disbursed", "submitted", "draft"]


class StatisticRow(BaseModel):
    operator_id: int
    operator_name: str
    credit_code: str | None
    station_id: int | None
    station_name: str | None
    station_code: str | None
    source: str | None
    equipment_id: int | None
    equipment_external_id: str | None
    equipment_name: str | None
    day: str | None
    claim_count: int
    device_rows: int
    eligible_kwh: DecimalText
    base_amount: DecimalText
    initial_amount: DecimalText | None
    adjustment_amount: DecimalText | None
    adjusted_amount: DecimalText | None
    is_demo: bool


class StatisticTotals(BaseModel):
    claim_count: int
    legacy_claim_count: int
    device_claim_count: int
    eligible_kwh: DecimalText
    base_amount: DecimalText
    initial_amount: DecimalText
    adjustment_amount: DecimalText
    adjusted_amount: DecimalText


class StatisticPage(BaseModel):
    items: list[StatisticRow]
    total: int
    page: int
    page_size: int
    dimension: Dimension
    status: Status
    start_date: str
    end_date: str
    totals: StatisticTotals
    notice: str


def collect(
    db,
    user,
    start_date,
    end_date,
    dimension,
    status,
    operator_id=None,
    station_id=None,
    *,
    station_ids=None,
    separate_demo=False,
):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "仅监管及所属企业可查看补贴统计")
    if start_date > end_date or (end_date - start_date).days > 365:
        raise HTTPException(422, "统计范围最多366天且结束不早于开始")
    stmt = station_scope(
        select(SubsidyClaim, Station, Operator)
        .join(Station, Station.id == SubsidyClaim.station_id)
        .join(Operator, Operator.id == Station.operator_id)
        .where(
            SubsidyClaim.operator_id == Station.operator_id,
            SubsidyClaim.start_date >= start_date,
            SubsidyClaim.end_date <= end_date,
            SubsidyClaim.status.in_(STATUSES[status]),
        ),
        user,
    )
    if operator_id is not None:
        stmt = stmt.where(Operator.id == operator_id)
    if station_id is not None:
        stmt = stmt.where(Station.id == station_id)
    if station_ids is not None:
        stmt = stmt.where(Station.id.in_(station_ids))
    claims = list(db.execute(stmt.order_by(SubsidyClaim.id).limit(CLAIM_LIMIT + 1)))
    if len(claims) > CLAIM_LIMIT:
        raise HTTPException(422, "匹配申请超过10000条，请缩小统计范围；未返回部分结果")
    groups = {}
    totals = {
        "claim_count": len(claims),
        "legacy_claim_count": 0,
        "device_claim_count": 0,
        "eligible_kwh": Decimal(0),
        "base_amount": Decimal(0),
        "initial_amount": Decimal(0),
        "adjustment_amount": Decimal(0),
        "adjusted_amount": Decimal(0),
    }
    device_claims = {}

    def group(key, claim, station, company, line=None):
        if key not in groups:
            is_device = line is not None
            groups[key] = {
                "operator_id": company.id,
                "operator_name": company.name,
                "credit_code": company.credit_code,
                "station_id": station.id if dimension != "operator" else None,
                "station_name": station.name if dimension != "operator" else None,
                "station_code": station.external_id if dimension != "operator" else None,
                "source": station.source if dimension != "operator" else None,
                "equipment_id": line.equipment_id if line else None,
                "equipment_external_id": line.source.equipment_external_id if line else None,
                "equipment_name": line.source.equipment_name if line else None,
                "day": str(line.source.day) if dimension == "device_day" else None,
                "claims": set(),
                "device_rows": 0,
                "eligible_kwh": Decimal(0),
                "base_amount": Decimal(0),
                "initial_amount": None if is_device else Decimal(0),
                "adjustment_amount": None if is_device else Decimal(0),
                "adjusted_amount": None if is_device else Decimal(0),
                "is_demo": True,
            }
        row = groups[key]
        row["claims"].add(claim.id)
        row["is_demo"] = row["is_demo"] and claim.is_demo
        return row

    with localcontext() as context:
        context.prec = 50
        for claim, station, company in claims:
            calculation = (claim.snapshot or {}).get("device_calculation")
            if calculation is not None:
                if separate_demo and not claim.is_demo:
                    raise HTTPException(409, "当前设备冻结模型仅支持演示核算，不能标记为正式数据")
                device_claims[claim.id] = (claim, station, company)
                totals["device_claim_count"] += 1
                try:
                    validated = DeviceClaimCalculation.model_validate(calculation)
                    initial = Decimal(validated.calculated_amount)
                    if not initial.is_finite():
                        raise ValueError
                except (KeyError, TypeError, ValueError, ArithmeticError) as error:
                    raise HTTPException(409, "核算快照损坏，未生成统计") from error
            else:
                totals["legacy_claim_count"] += 1
                initial = claim.base_amount
            values = {
                "eligible_kwh": claim.total_kwh,
                "base_amount": claim.base_amount,
                "initial_amount": initial,
                "adjustment_amount": claim.adjusted_amount - initial,
                "adjusted_amount": claim.adjusted_amount,
            }
            for key, value in values.items():
                totals[key] += value
            if dimension in {"operator", "station"}:
                key = (company.id, station.id if dimension == "station" else 0)
                if separate_demo:
                    key += (claim.is_demo,)
                row = group(key, claim, station, company)
                for field, value in values.items():
                    row[field] += value
        if dimension in {"equipment", "device_day"} and device_claims:
            lines = list(
                db.scalars(
                    select(SubsidyDeviceLine)
                    .where(SubsidyDeviceLine.claim_id.in_(device_claims))
                    .order_by(SubsidyDeviceLine.claim_id, SubsidyDeviceLine.fact_id)
                    .limit(LINE_LIMIT + 1)
                )
            )
            if len(lines) > LINE_LIMIT:
                raise HTTPException(422, "冻结设备明细超过50000条，请缩小范围；未返回部分统计")
            counts = {cid: 0 for cid in device_claims}
            sums = {cid: [Decimal(0), Decimal(0)] for cid in device_claims}
            for item in lines:
                try:
                    line = DeviceLineCalculation.model_validate(item.snapshot)
                except ValidationError as error:
                    raise HTTPException(409, "冻结设备明细损坏，未生成统计") from error
                claim, station, company = device_claims[item.claim_id]
                if (
                    line.fact_id != item.fact_id
                    or line.source.energy_kwh is None
                    or not line.source.energy_complete
                ):
                    raise HTTPException(409, "冻结设备明细缺失完整计量，未生成统计")
                if not claim.start_date <= line.source.day <= claim.end_date:
                    raise HTTPException(409, "冻结设备日期超出申请周期，未生成统计")
                counts[item.claim_id] += 1
                sums[item.claim_id][0] += Decimal(line.amount)
                sums[item.claim_id][1] += line.source.energy_kwh if line.eligible else Decimal(0)
                key = (
                    company.id,
                    station.id,
                    line.equipment_id,
                    str(line.source.day) if dimension == "device_day" else "",
                )
                if separate_demo:
                    key += (claim.is_demo,)
                row = group(key, claim, station, company, line)
                row["device_rows"] += 1
                row["eligible_kwh"] += line.source.energy_kwh if line.eligible else Decimal(0)
                row["base_amount"] += Decimal(line.amount)
            if any(
                counts[cid] != row[0].snapshot["device_calculation"]["row_count"]
                or sums[cid] != [row[0].base_amount, row[0].total_kwh]
                for cid, row in device_claims.items()
            ):
                raise HTTPException(409, "冻结设备明细不完整，未生成统计")
        output = []
        for key in sorted(groups):
            row = groups[key]
            row["claim_count"] = len(row.pop("claims"))
            for field, value in row.items():
                if isinstance(value, Decimal):
                    row[field] = format(value, "f")
            output.append(StatisticRow.model_validate(row))
        for field, value in totals.items():
            if isinstance(value, Decimal):
                totals[field] = format(value, "f")
    return output, StatisticTotals.model_validate(totals)
