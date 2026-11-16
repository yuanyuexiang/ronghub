"""Executable demonstration device rules: closed cohorts, historical rating and frozen lines."""

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import delete, select

from app.business_audit import state, transition
from app.models import (
    SubsidyAction,
    SubsidyClaim,
    SubsidyDeviceAllocation,
    SubsidyDeviceDay,
    SubsidyDeviceLine,
    SubsidyDeviceScope,
    SubsidyPolicy,
)
from app.rating_results import local_day
from app.subsidy_device_days import SourceDeviceDay
from app.subsidy_device_scopes import SourceDeviceScope
from app.subsidy_execution_contracts import (
    DeviceClaimCalculation,
    DeviceLineCalculation,
    FrozenDeviceScope,
)
from app.subsidy_rating_history import at_period_end
from app.subsidy_rules import DeviceExecutableRules

MAX_ROWS = 50000
CENT = Decimal("0.01")
MAX_MONEY = Decimal("9999999999999999.99")
MAX_KWH = Decimal("999999999999.999999")


def money(value):
    if not value.is_finite():
        raise HTTPException(422, "核算金额超出可保存范围，整单未提交")
    try:
        result = value.quantize(CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation as error:
        raise HTTPException(422, "核算金额超出可保存范围，整单未提交") from error
    if not result.is_finite() or result < 0 or result > MAX_MONEY:
        raise HTTPException(422, "核算金额超出可保存范围，整单未提交")
    return result


def overlapping(db, station_id, start, end, *, device):
    stmt = select(SubsidyClaim.id).where(
        SubsidyClaim.station_id == station_id,
        SubsidyClaim.status.notin_(["withdrawn", "rejected"]),
        SubsidyClaim.start_date <= end,
        SubsidyClaim.end_date >= start,
    )
    if not device:
        stmt = stmt.where(SubsidyClaim.snapshot["device_calculation"].as_string().is_not(None))
    if db.scalar(stmt.limit(1)):
        raise HTTPException(409, "周期与已有设备核算或其他口径申请重叠，请先核对原申请；未重复占用")


def scope_snapshot(scope):
    return FrozenDeviceScope(
        id=scope.id,
        version=scope.version,
        external_id=scope.external_id,
        day=scope.day.isoformat(),
        equipment_external_ids=scope.equipment_external_ids,
        source_updated_at=scope.source_updated_at.isoformat(),
        is_demo=scope.is_demo,
    )


def observations(db, station, start, end):
    scopes = list(
        db.scalars(
            select(SubsidyDeviceScope)
            .where(
                SubsidyDeviceScope.station_id == station.id,
                SubsidyDeviceScope.source == station.source,
                SubsidyDeviceScope.operator_id == station.operator_id,
                SubsidyDeviceScope.day >= start,
                SubsidyDeviceScope.day <= end,
            )
            .order_by(SubsidyDeviceScope.day)
        )
    )
    expected = {start + timedelta(days=offset) for offset in range((end - start).days + 1)}
    if {scope.day for scope in scopes} != expected:
        raise HTTPException(409, "周期内缺少来源明确的每日设备集合；未用当前台账补历史范围")
    rows = list(
        db.scalars(
            select(SubsidyDeviceDay)
            .where(
                SubsidyDeviceDay.station_id == station.id,
                SubsidyDeviceDay.source == station.source,
                SubsidyDeviceDay.operator_id == station.operator_id,
                SubsidyDeviceDay.day >= start,
                SubsidyDeviceDay.day <= end,
            )
            .order_by(SubsidyDeviceDay.id)
            .limit(MAX_ROWS + 1)
        )
    )
    if len(rows) > MAX_ROWS:
        raise HTTPException(422, "单站单次设备日核算最多50000行，请分期申报")
    by_day = {day: [] for day in expected}
    for row in rows:
        by_day[row.day].append(row)
    for scope in scopes:
        try:
            SourceDeviceScope.model_validate(
                {key: getattr(scope, key) for key in SourceDeviceScope.model_fields}
            )
        except ValidationError as error:
            raise HTTPException(409, "设备集合来源证据不完整") from error
        current = by_day[scope.day]
        if (
            not scope.complete
            or not scope.is_demo
            or {row.equipment_external_id for row in current} != set(scope.equipment_external_ids)
            or any(
                row.scope_id != scope.id
                or row.scope_external_id != scope.external_id
                or not row.is_demo
                for row in current
            )
        ):
            raise HTTPException(409, "设备集合不完整、观测未关联或混入非演示数据，整单未核算")
    return scopes, rows


def raw_observation(row):
    try:
        return SourceDeviceDay.model_validate(
            {key: getattr(row, key) for key in SourceDeviceDay.model_fields}
        )
    except ValidationError as error:
        raise HTTPException(409, "设备原始观测未通过完整性校验") from error


def line_calculation(row, scope, rules, charging_rate):
    raw = raw_observation(row)
    if (
        not raw.energy_complete
        or raw.energy_kwh is None
        or raw.business_kind == "unknown"
        or raw.metric_basis != rules.metric_basis
        or any(
            getattr(raw, axis + "_observed_seconds") != 86400
            or getattr(raw, axis + "_unknown_seconds") != 0
            for axis in ["fault", "offline"]
        )
    ):
        raise HTTPException(409, "设备计量或状态覆盖未知/不完整；未置零或默认为达标")
    fault = raw.fault_seconds * 100 >= rules.fault_rate_below * 86400
    offline = raw.offline_seconds * 100 >= rules.offline_rate_below * 86400
    reason = (
        "both_thresholds"
        if fault and offline
        else "fault_threshold"
        if fault
        else "offline_threshold"
        if offline
        else None
    )
    rate = charging_rate if raw.business_kind == "charging" else rules.swap_rate
    amount = money(raw.energy_kwh * rate if reason is None else Decimal(0))
    return DeviceLineCalculation(
        fact_id=row.id,
        fact_version=row.version,
        equipment_id=row.equipment_id,
        scope_id=scope.id,
        scope_version=scope.version,
        source=raw,
        is_demo=row.is_demo,
        eligible=reason is None,
        exclusion_reason=reason,
        rate=str(rate),
        amount=str(amount),
    )


def create(db, user, station, policy, start, end, note):
    if (end - start).days > 365 or end >= local_day():
        raise HTTPException(422, "设备核算仅支持已结束的北京时间周期，单次最多366天")
    try:
        rules = DeviceExecutableRules.model_validate(policy.device_rules)
    except ValidationError as error:
        raise HTTPException(409, "设备规则尚未具备完整执行口径") from error
    overlapping(db, station.id, start, end, device=True)
    scopes, rows = observations(db, station, start, end)
    if not rows:
        raise HTTPException(409, "周期内没有来源明确的设备计量")
    rating = at_period_end(db, station, end)
    if db.scalar(
        select(SubsidyDeviceAllocation.fact_id)
        .where(SubsidyDeviceAllocation.fact_id.in_([row.id for row in rows]))
        .limit(1)
    ):
        raise HTTPException(409, "设备计量已被其他申请占用")
    scope_by_day = {scope.day: scope for scope in scopes}
    with localcontext() as context:
        context.prec = 50
        lines = [line_calculation(row, scope_by_day[row.day], rules, policy.rate) for row in rows]
        observed = sum((line.source.energy_kwh for line in lines), Decimal(0))
        eligible = sum((line.source.energy_kwh for line in lines if line.eligible), Decimal(0))
        if eligible <= 0:
            raise HTTPException(409, "区间内没有符合门槛的正电量，未创建零值占用")
        if eligible > MAX_KWH:
            raise HTTPException(422, "累计有效电量超出保存范围，整单未提交")
        base = money(sum((Decimal(line.amount) for line in lines), Decimal(0)))
        coefficient = rules.star_coefficients[str(rating.stars)]
        calculated = money(base * coefficient)
        frozen = DeviceClaimCalculation(
            execution_version=2,
            policy_rules=rules,
            rating=rating,
            star_coefficient=str(coefficient),
            pre_coefficient_amount=str(base),
            calculated_amount=str(calculated),
            observed_kwh=str(observed),
            row_count=len(lines),
            eligible_rows=sum(line.eligible for line in lines),
            excluded_rows=sum(not line.eligible for line in lines),
            scopes=[scope_snapshot(scope) for scope in scopes],
        )
    claim = SubsidyClaim(
        application_note=note,
        station_id=station.id,
        operator_id=user.operator_id,
        policy_id=policy.id,
        start_date=start,
        end_date=end,
        total_kwh=eligible,
        base_amount=base,
        adjusted_amount=calculated,
        snapshot={
            "policy_code": policy.code,
            "policy_version": policy.version,
            "policy_rate": str(policy.rate),
            "rounding": rules.rounding,
            "device_calculation": frozen.model_dump(mode="json"),
        },
    )
    db.add(claim)
    db.flush()
    db.add_all(
        [
            SubsidyDeviceLine(
                claim_id=claim.id, fact_id=line.fact_id, snapshot=line.model_dump(mode="json")
            )
            for line in lines
        ]
    )
    db.flush()
    db.add_all([SubsidyDeviceAllocation(claim_id=claim.id, fact_id=line.fact_id) for line in lines])
    db.add(
        SubsidyAction(
            claim_id=claim.id,
            actor_id=user.id,
            action="create",
            comment="演示设备日计量/门槛/周期末公示系数核算与占用",
            transition_snapshot={"before": None, "after": state(claim)},
        )
    )
    transition(db, user, "subsidy.create", claim.id, None, state(claim), "subsidy_claim")
    return claim


def verify(db, claim, station):
    value = (claim.snapshot or {}).get("device_calculation")
    if value is None:
        return
    try:
        calculation = DeviceClaimCalculation.model_validate(value)
    except ValidationError as error:
        raise HTTPException(409, "设备核算快照损坏，未提交操作") from error
    policy = db.get(SubsidyPolicy, claim.policy_id)
    try:
        same_values = bool(policy) and (
            Decimal(claim.snapshot["policy_rate"]) == policy.rate
            and DeviceExecutableRules.model_validate(policy.device_rules)
            == calculation.policy_rules
        )
    except (ValidationError, InvalidOperation, KeyError, TypeError, ValueError):
        same_values = False
    if (
        not policy
        or policy.code != claim.snapshot.get("policy_code")
        or policy.version != claim.snapshot.get("policy_version")
        or not same_values
    ):
        raise HTTPException(409, "原政策版本与冻结规则不一致")
    if (
        Decimal(calculation.star_coefficient)
        != calculation.policy_rules.star_coefficients[str(calculation.rating.stars)]
    ):
        raise HTTPException(409, "星级系数与冻结政策不一致")
    scopes, rows = observations(db, station, claim.start_date, claim.end_date)
    if [scope_snapshot(scope).model_dump(mode="json") for scope in scopes] != [
        scope.model_dump(mode="json") for scope in calculation.scopes
    ]:
        raise HTTPException(409, "来源设备集合已修订，请核对后撤回重报")
    if at_period_end(db, station, claim.end_date) != calculation.rating:
        raise HTTPException(409, "历史公示依据已变化，请核对原申请")
    lines = list(
        db.scalars(
            select(SubsidyDeviceLine)
            .where(SubsidyDeviceLine.claim_id == claim.id)
            .order_by(SubsidyDeviceLine.fact_id)
        )
    )
    allocated = set(
        db.scalars(
            select(SubsidyDeviceAllocation.fact_id).where(
                SubsidyDeviceAllocation.claim_id == claim.id
            )
        )
    )
    if (
        len(lines) != calculation.row_count
        or [line.fact_id for line in lines] != [row.id for row in rows]
        or allocated != {line.fact_id for line in lines}
    ):
        raise HTTPException(409, "设备核算明细或独占分配不完整")
    scope_by_day = {scope.day: scope for scope in scopes}
    with localcontext() as context:
        context.prec = 50
        current = [
            line_calculation(
                row,
                scope_by_day[row.day],
                calculation.policy_rules,
                Decimal(claim.snapshot["policy_rate"]),
            )
            for row in rows
        ]
        if [line.model_dump(mode="json") for line in current] != [line.snapshot for line in lines]:
            raise HTTPException(409, "设备原始观测已修订，请核对历史补差；未重算原申请")
        if (
            calculation.eligible_rows != sum(line.eligible for line in current)
            or calculation.excluded_rows != sum(not line.eligible for line in current)
            or Decimal(calculation.observed_kwh)
            != sum((line.source.energy_kwh for line in current), Decimal(0))
        ):
            raise HTTPException(409, "设备核算汇总与冻结明细不一致")
        base = money(sum((Decimal(line.amount) for line in current), Decimal(0)))
        eligible = sum((line.source.energy_kwh for line in current if line.eligible), Decimal(0))
        if (
            str(base) != calculation.pre_coefficient_amount
            or base != claim.base_amount
            or eligible != claim.total_kwh
            or money(base * Decimal(calculation.star_coefficient))
            != Decimal(calculation.calculated_amount)
        ):
            raise HTTPException(409, "设备核算金额快照不一致")


def release(db, claim):
    db.execute(delete(SubsidyDeviceAllocation).where(SubsidyDeviceAllocation.claim_id == claim.id))
