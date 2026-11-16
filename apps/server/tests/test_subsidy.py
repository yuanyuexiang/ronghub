import secrets
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select
from subsidy_samples import create_claim as sample_claim

from app import subsidy
from app.db import SessionLocal
from app.models import (
    EnergyAllocation,
    EnergyRecord,
    Station,
    SubsidyAction,
    SubsidyAttachment,
    SubsidyClaim,
    SubsidyPolicy,
    User,
)


@pytest.fixture()
def finance(accounts, monkeypatch, tmp_path):
    from app.config import settings

    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    names, _, sids = accounts
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.username == names[0]))
        uid = user.id
        db.get(Station, sids[0]).status = "operating"
        p = SubsidyPolicy(
            code="test-" + secrets.token_hex(8),
            version=1,
            name="测试示例政策",
            rate=Decimal("0.10"),
            effective_from=date(2026, 1, 1),
            effective_to=date(2026, 12, 31),
        )
        db.add(p)
        db.flush()
        pid = p.id
        for i, kwh in enumerate(["10.05", "20.05"], 1):
            db.add(
                EnergyRecord(
                    station_id=sids[0],
                    source="test-subsidy",
                    external_id=f"{pid}-{i}",
                    day=date(2026, 11, i),
                    kwh=Decimal(kwh),
                )
            )
    yield uid, sids[0], pid
    with SessionLocal.begin() as db:
        cids = select(SubsidyClaim.id).where(SubsidyClaim.policy_id == pid)
        db.execute(delete(SubsidyAttachment).where(SubsidyAttachment.claim_id.in_(cids)))
        db.execute(delete(SubsidyAction).where(SubsidyAction.claim_id.in_(cids)))
        db.execute(delete(EnergyAllocation).where(EnergyAllocation.claim_id.in_(cids)))
        db.execute(delete(SubsidyClaim).where(SubsidyClaim.policy_id == pid))
        db.execute(delete(EnergyRecord).where(EnergyRecord.station_id == sids[0]))
        db.execute(delete(SubsidyPolicy).where(SubsidyPolicy.id == pid))


def test_decimal_snapshot_allocation_adjustment_and_freeze(finance):
    uid, sid, pid = finance
    with SessionLocal.begin() as db:
        user = db.get(User, uid)
        reg = SimpleNamespace(id=uid, role="regulator")
        c = sample_claim(db, user, sid, pid, date(2026, 11, 1), date(2026, 11, 2))
        cid = c.id
        assert c.base_amount == Decimal("3.02")
        assert c.total_kwh == Decimal("30.100000")
        assert c.snapshot["details"][0]["amount"] == "1.01"
        with pytest.raises(HTTPException) as e:
            sample_claim(db, user, sid, pid, date(2026, 11, 1), date(2026, 11, 2))
        assert e.value.status_code == 409
        subsidy.act(db, user, cid, "submit", 1, "")
        with pytest.raises(HTTPException) as e:
            subsidy.act(db, user, cid, "approve", 2, "越权")
        assert e.value.status_code == 403
        with pytest.raises(HTTPException) as e:
            subsidy.act(db, reg, cid, "adjust", 2, "无效负额", Decimal(-10))
        assert e.value.status_code == 422
        subsidy.act(db, reg, cid, "adjust", 2, "校正示例", Decimal("-0.02"))
        assert c.adjusted_amount == Decimal("3.00")
        with pytest.raises(HTTPException) as e:
            subsidy.act(db, reg, cid, "approve", 2, "过期版本")
        assert e.value.status_code == 409
        subsidy.act(db, reg, cid, "approve", 3, "审核通过")
        with pytest.raises(HTTPException) as e:
            subsidy.act(db, reg, cid, "adjust", 4, "审定后修改", Decimal(1))
        assert e.value.status_code == 409
        subsidy.act(db, reg, cid, "record_disbursement", 4, "演示发放凭证登记，不代表支付")
        assert c.status == "disbursed"
        assert c.snapshot["policy_rate"] == "0.100000"
        assert (
            db.scalar(
                select(func.count())
                .select_from(EnergyAllocation)
                .where(EnergyAllocation.claim_id == cid)
            )
            == 2
        )


def test_withdrawal_releases_energy_and_foreign_scope(finance, accounts):
    uid, sid, pid = finance
    names, _, _ = accounts
    with SessionLocal.begin() as db:
        user = db.get(User, uid)
        other = db.scalar(select(User).where(User.username == names[1]))
        with pytest.raises(HTTPException) as e:
            sample_claim(db, other, sid, pid, date(2026, 11, 1), date(2026, 11, 2))
        assert e.value.status_code == 404
        with pytest.raises(HTTPException) as e:
            sample_claim(db, user, sid, pid, date(2025, 1, 1), date(2026, 11, 2))
        assert e.value.status_code == 422
        c = sample_claim(db, user, sid, pid, date(2026, 11, 1), date(2026, 11, 2))
        with pytest.raises(HTTPException) as e:
            subsidy.claim_access(db, other, c.id)
        assert e.value.status_code == 404
        subsidy.act(db, user, c.id, "withdraw", 1, "撤回示例")
        db.flush()
        new = sample_claim(db, user, sid, pid, date(2026, 11, 1), date(2026, 11, 2))
        assert new.id != c.id and new.base_amount == c.base_amount


def test_amount_audit_commits_atomically_and_is_searchable(finance, accounts, staff):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.models import Audit

    uid, sid, pid = finance
    with SessionLocal.begin() as db:
        user = db.get(User, uid)
        reg = SimpleNamespace(id=uid, role="regulator")
        claim = sample_claim(db, user, sid, pid, date(2026, 11, 1), date(2026, 11, 2))
        cid = claim.id
        subsidy.act(db, user, cid, "submit", 1, "")
    with SessionLocal() as db:
        subsidy.act(db, reg, cid, "adjust", 2, "private-note-must-not-be-audited", Decimal("0.03"))
        db.flush()
        assert db.scalar(
            select(Audit.id).where(Audit.action == "subsidy.adjust", Audit.object_id == cid)
        )
        db.rollback()
    with SessionLocal.begin() as db:
        assert db.get(SubsidyClaim, cid).adjusted_amount == Decimal("3.02")
        assert (
            db.scalar(
                select(Audit.id).where(Audit.action == "subsidy.adjust", Audit.object_id == cid)
            )
            is None
        )
        subsidy.act(db, reg, cid, "adjust", 2, "private-note-must-not-be-audited", Decimal("0.03"))
    with TestClient(app, client=("audit-amount-" + secrets.token_hex(8), 1)) as client:
        assert (
            client.post(
                "/api/v1/auth/login", json={"username": staff[0][0], "password": accounts[1]}
            ).status_code
            == 200
        )
        response = client.get(
            "/api/v1/audits", params={"action": "subsidy.adjust", "object_id": cid}
        )
        assert response.status_code == 200
        row = response.json()["items"][0]
        assert response.json()["total"] == 1 and row["result"] == "committed"
        assert row["detail"]["object_type"] == "subsidy_claim"
        assert row["detail"]["before"]["adjusted_amount"] == "3.02"
        assert row["detail"]["after"]["adjusted_amount"] == "3.05"
        assert row["detail"]["before"]["version"] == 2
        assert row["detail"]["after"]["version"] == 3
        assert "private-note" not in response.text
