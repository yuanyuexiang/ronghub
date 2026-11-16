"""Demonstration-only subsidy rules; exact decimal arithmetic and immutable evidence."""

from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from sqlalchemy import delete, select

from app.business_audit import state, transition
from app.config import settings
from app.models import (
    EnergyAllocation,
    EnergyRecord,
    Station,
    SubsidyAction,
    SubsidyClaim,
    SubsidyPolicy,
)
from app.scopes import records as scoped_records
from app.scopes import station_scope

CENT = Decimal("0.01")


def demo_only():
    if not settings.demo_mode:
        raise HTTPException(409, "补贴政策未确认，示例核算仅限演示环境")


def create_claim(db, user, station_id, policy_id, start, end, application_note=""):
    demo_only()
    from app.subsidy_evidence import validate_note

    validate_note(application_note)
    if user.role != "operator" or user.operator_id is None:
        raise HTTPException(403, "仅所属运营商可申报")
    station = db.scalar(
        station_scope(select(Station), user)
        .where(Station.id == station_id, Station.operator_id == user.operator_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not station:
        raise HTTPException(404, "场站不存在或不可访问")
    if station.status != "operating":
        raise HTTPException(409, "示例规则要求场站已投运")
    policy = db.get(SubsidyPolicy, policy_id)
    if not policy or not policy.enabled or not policy.is_demo:
        raise HTTPException(409, "演示政策不存在、停用或不适用")
    if policy.device_rules is not None and policy.device_rules.get("configuration_version") != 2:
        raise HTTPException(
            409, "设备补贴规则已配置，设备计量、门槛及历史星级核算尚未接通；未套用旧站级算法"
        )
    if start > end or start < policy.effective_from or end > policy.effective_to:
        raise HTTPException(422, "申报区间必须在政策有效期内")
    from app import subsidy_device_execution as device

    if policy.device_rules is not None:
        return device.create(db, user, station, policy, start, end, application_note)
    device.overlapping(db, station.id, start, end, device=False)
    records = list(
        db.scalars(
            select(EnergyRecord)
            .where(
                EnergyRecord.station_id == station_id,
                EnergyRecord.day >= start,
                EnergyRecord.day <= end,
                EnergyRecord.eligible.is_(True),
                EnergyRecord.kwh > 0,
            )
            .order_by(EnergyRecord.id)
            .with_for_update()
        )
    )
    if not records:
        raise HTTPException(409, "区间内没有可核算的有效电量")
    if db.scalar(
        select(EnergyAllocation.energy_id)
        .where(EnergyAllocation.energy_id.in_([e.id for e in records]))
        .limit(1)
    ):
        raise HTTPException(409, "区间包含已占用电量，请勿重复申报；历史补差需另行处理")
    detail = [
        {
            "energy_id": e.id,
            "day": e.day.isoformat(),
            "kwh": str(e.kwh),
            "rate": str(policy.rate),
            "amount": str((e.kwh * policy.rate).quantize(CENT, rounding=ROUND_HALF_UP)),
        }
        for e in records
    ]
    amount = sum((Decimal(d["amount"]) for d in detail), Decimal(0)).quantize(CENT)
    claim = SubsidyClaim(
        application_note=application_note,
        station_id=station_id,
        operator_id=user.operator_id,
        policy_id=policy_id,
        start_date=start,
        end_date=end,
        total_kwh=sum((e.kwh for e in records), Decimal(0)),
        base_amount=amount,
        adjusted_amount=amount,
        snapshot={
            "policy_code": policy.code,
            "policy_version": policy.version,
            "policy_rate": str(policy.rate),
            "rounding": "HALF_UP_PER_ENERGY_RECORD_2DP",
            "details": detail,
        },
    )
    db.add(claim)
    db.flush()
    for e in records:
        db.add(EnergyAllocation(energy_id=e.id, claim_id=claim.id))
    db.add(
        SubsidyAction(
            claim_id=claim.id,
            actor_id=user.id,
            action="create",
            comment="示例电量核算与占用",
            transition_snapshot={"before": None, "after": state(claim)},
        )
    )
    transition(db, user, "subsidy.create", claim.id, None, state(claim), "subsidy_claim")
    return claim


def claim_access(db, user, cid, lock=False):
    stmt = scoped_records(select(SubsidyClaim), SubsidyClaim, user).where(SubsidyClaim.id == cid)
    c = db.scalar(
        stmt.with_for_update().execution_options(populate_existing=True) if lock else stmt
    )
    if not c or (user.role == "operator" and c.operator_id != user.operator_id):
        raise HTTPException(404, "补贴申请不存在或不可访问")
    if user.role not in {"operator", "regulator"}:
        raise HTTPException(403, "无权访问补贴申请")
    return c


def act(db, user, cid, action, version, comment, delta=Decimal(0)):
    demo_only()
    from app import subsidy_device_execution as device

    preliminary = claim_access(db, user, cid)
    device_station = None
    if (preliminary.snapshot or {}).get("device_calculation") is not None:
        device_station = db.scalar(
            select(Station)
            .where(Station.id == preliminary.station_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    c = claim_access(db, user, cid, True)
    before = state(c)
    if c.version != version:
        raise HTTPException(409, "申请版本已更新，请刷新")
    if action == "submit":
        if user.role != "operator":
            raise HTTPException(403, "仅运营商可提交")
        if c.status != "draft":
            raise HTTPException(409, "仅草稿可提交")
        from app.subsidy_evidence import freeze

        if device_station is not None:
            device.verify(db, c, device_station)
        freeze(db, c)
        c.status = "submitted"
    elif action == "withdraw":
        if user.role != "operator":
            raise HTTPException(403, "仅运营商可撤回")
        if c.status not in {"draft", "submitted"}:
            raise HTTPException(409, "当前状态不可撤回")
        c.status = "withdrawn"
    elif action in {"adjust", "approve", "reject", "record_disbursement"}:
        if user.role != "regulator":
            raise HTTPException(403, "仅监管人员可审核或登记")
        if not comment.strip():
            raise HTTPException(422, "请填写依据或凭证说明")
        if action != "reject":
            from app.subsidy_evidence import verify_submission

            if device_station is not None:
                device.verify(db, c, device_station)
            verify_submission(db, c)
        if action == "record_disbursement":
            if c.status != "approved":
                raise HTTPException(409, "仅通过的补贴可登记发放")
            c.status = "disbursed"
        else:
            if c.status != "submitted":
                raise HTTPException(409, "仅待审核申请可处理")
            if action == "adjust":
                if (
                    not delta.is_finite()
                    or delta.quantize(CENT) != delta
                    or c.adjusted_amount + delta < 0
                ):
                    raise HTTPException(422, "调整额必须精确到分且结果不能小于零")
                if c.adjusted_amount + delta > device.MAX_MONEY:
                    raise HTTPException(422, "调整结果超出金额保存范围")
                c.adjusted_amount += delta
            else:
                c.status = "approved" if action == "approve" else "rejected"
    else:
        raise HTTPException(422, "不支持的补贴动作")
    if c.status in {"withdrawn", "rejected"}:
        db.execute(delete(EnergyAllocation).where(EnergyAllocation.claim_id == c.id))
        device.release(db, c)
    c.version += 1
    db.add(
        SubsidyAction(
            transition_snapshot={"before": before, "after": state(c)},
            claim_id=c.id,
            actor_id=user.id,
            action=action,
            comment=comment,
            delta=delta if action == "adjust" else Decimal(0),
        )
    )
    transition(db, user, "subsidy." + action, c.id, before, state(c), "subsidy_claim")
    return c


def output(c):
    return {
        "application_note": c.application_note,
        "id": c.id,
        "station_id": c.station_id,
        "operator_id": c.operator_id,
        "policy_id": c.policy_id,
        "start_date": c.start_date.isoformat(),
        "end_date": c.end_date.isoformat(),
        "status": c.status,
        "version": c.version,
        "total_kwh": str(c.total_kwh),
        "base_amount": str(c.base_amount),
        "adjusted_amount": str(c.adjusted_amount),
        "is_demo": c.is_demo,
        "created_at": c.created_at.isoformat(),
    }
