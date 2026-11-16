# ruff: noqa: F811 - pytest fixture parameters shadow fixture imports
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Barrier

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from subsidy_samples import create_claim as sample_claim
from test_business_exports import login, report_staff  # noqa: F401
from test_subsidy import finance  # noqa: F401

from app import subsidy
from app.config import settings
from app.db import SessionLocal
from app.models import (
    Audit,
    EnergyAllocation,
    EnergyRecord,
    LoginSession,
    RoleProfile,
    Station,
    SubsidyAction,
    SubsidyClaim,
    User,
)

PATH = "/api/v1/subsidy/claims/batch-actions"


@pytest.fixture
def submitted(finance):
    uid, station, policy = finance
    ids = []
    with SessionLocal.begin() as db:
        user = db.get(User, uid)
        for day in [1, 2]:
            claim = sample_claim(
                db, user, station, policy, date(2026, 11, day), date(2026, 11, day)
            )
            subsidy.act(db, user, claim.id, "submit", 1, "")
            ids.append(claim.id)
    return ids


def csrf(client):
    return {"x-csrf-token": client.get("/api/v1/auth/me").json()["csrf_token"]}


def body(ids, action="approve"):
    return {
        "action": action,
        "items": [{"id": cid, "version": 2} for cid in reversed(ids)],
        "comment": "核验材料后统一办理",
    }


def state(ids):
    with SessionLocal() as db:
        records = list(
            db.scalars(
                select(SubsidyClaim).where(SubsidyClaim.id.in_(ids)).order_by(SubsidyClaim.id)
            )
        )
        return (
            [
                (row.id, row.status, row.version, str(row.base_amount), str(row.adjusted_amount))
                for row in records
            ],
            db.scalar(
                select(func.count())
                .select_from(EnergyAllocation)
                .where(EnergyAllocation.claim_id.in_(ids))
            ),
            db.scalar(
                select(func.count())
                .select_from(SubsidyAction)
                .where(SubsidyAction.claim_id.in_(ids))
            ),
        )


def summary_count(user_id):
    with SessionLocal() as db:
        return db.scalar(
            select(func.count())
            .select_from(Audit)
            .where(Audit.user_id == user_id, Audit.action == "subsidy.batch_review")
        )


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_batch_shares_review_rules_and_preserves_money_with_per_record_history(
    submitted, accounts, report_staff, decision
):
    client = login(report_staff[0][0], report_staff[1])
    with SessionLocal() as db:
        uid = db.scalar(select(User.id).where(User.username == report_staff[0][0]))
    before, _, _ = state(submitted)
    response = client.post(PATH, json=body(submitted, decision), headers=csrf(client))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["total"] == 2 and [row["id"] for row in result["items"]] == sorted(submitted)
    after, allocations, actions = state(submitted)
    assert [row[1:3] for row in after] == [
        ("approved" if decision == "approve" else "rejected", 3)
    ] * 2
    assert [row[3:] for row in after] == [row[3:] for row in before]
    assert allocations == (2 if decision == "approve" else 0) and actions == 6
    assert summary_count(uid) == 1
    audit_page = client.get("/api/v1/audits", params={"action": "subsidy.batch_review"})
    assert audit_page.status_code == 200
    assert audit_page.json()["items"][0]["detail"] == {
        "decision": decision,
        "count": 2,
        "claims": [
            {"id": cid, "before_version": 2, "after_version": 3} for cid in sorted(submitted)
        ],
    }
    with SessionLocal() as db:
        history = list(
            db.scalars(
                select(SubsidyAction).where(
                    SubsidyAction.claim_id.in_(submitted), SubsidyAction.action == decision
                )
            )
        )
        assert len(history) == 2
        for action in history:
            assert action.actor_id == uid and action.comment == body(submitted)["comment"]
            assert action.delta == 0
            assert action.transition_snapshot["before"]["status"] == "submitted"
            assert action.transition_snapshot["after"]["status"] == (
                "approved" if decision == "approve" else "rejected"
            )
        audit = db.scalar(
            select(Audit).where(Audit.user_id == uid, Audit.action == "subsidy.batch_review")
        )
        assert audit.detail == {
            "decision": decision,
            "count": 2,
            "claims": [
                {"id": cid, "before_version": 2, "after_version": 3} for cid in sorted(submitted)
            ],
        }
    duplicate = client.post(PATH, json=body(submitted, decision), headers=csrf(client))
    assert duplicate.status_code == 409 and "整批未提交" in duplicate.text
    assert state(submitted) == (after, allocations, actions) and summary_count(uid) == 1


@pytest.mark.parametrize("failure", ["stale", "missing", "scope", "projection"])
def test_late_batch_failure_rolls_back_state_allocations_history_and_audits(
    submitted, finance, accounts, report_staff, monkeypatch, failure
):
    client = login(report_staff[0][0], report_staff[1])
    headers = csrf(client)
    payload = body(submitted, "reject")
    with SessionLocal() as db:
        uid = db.scalar(select(User.id).where(User.username == report_staff[0][0]))
    if failure == "stale":
        payload["items"][0]["version"] = 1
    elif failure == "missing":
        payload["items"][0]["id"] = 2147483647
    elif failure == "scope":
        with SessionLocal.begin() as db:
            user = db.get(User, uid)
            user.allowed_districts = ["可见区"]
            db.get(Station, finance[1]).district = "可见区"
            other = db.scalar(select(User).where(User.username == accounts[0][1]))
            record = SubsidyClaim(
                station_id=accounts[2][1],
                operator_id=other.operator_id,
                policy_id=finance[2],
                start_date=date(2026, 11, 1),
                end_date=date(2026, 11, 1),
                total_kwh=1,
                base_amount=1,
                adjusted_amount=1,
                snapshot={},
                status="submitted",
                version=2,
            )
            db.add(record)
            db.flush()
            payload["items"][0]["id"] = record.id
    else:
        original = subsidy.output
        monkeypatch.setattr(
            subsidy,
            "output",
            lambda record: (
                {**original(record), "adjusted_amount": 2.01}
                if record.id == max(submitted)
                else original(record)
            ),
        )
    before = state(submitted)
    if failure == "projection":
        with pytest.raises(ValidationError):
            client.post(PATH, json=payload, headers=headers)
    else:
        response = client.post(PATH, json=payload, headers=headers)
        assert response.status_code == (409 if failure == "stale" else 404), response.text
        assert "整批未提交" in response.text
    assert state(submitted) == before
    assert summary_count(uid) == 0
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(Audit)
                .where(Audit.user_id == uid, Audit.action == "subsidy.reject")
            )
            == 0
        )


@pytest.mark.parametrize(
    "bad",
    [
        "empty",
        "too_many",
        "duplicate",
        "bool_id",
        "string_version",
        "decimal_id",
        "oversize_id",
        "blank_comment",
        "adjust",
        "delta",
        "nested_extra",
    ],
)
def test_batch_input_is_bounded_strict_and_review_only(submitted, report_staff, bad):
    client = login(report_staff[0][0], report_staff[1])
    payload = body(submitted)
    if bad == "empty":
        payload["items"] = []
    elif bad == "too_many":
        payload["items"] = [{"id": i + 1, "version": 2} for i in range(101)]
    elif bad == "duplicate":
        payload["items"] = [payload["items"][0]] * 2
    elif bad == "bool_id":
        payload["items"][0]["id"] = True
    elif bad == "string_version":
        payload["items"][0]["version"] = "2"
    elif bad == "decimal_id":
        payload["items"][0]["id"] = 1.5
    elif bad == "oversize_id":
        payload["items"][0]["id"] = 2147483648
    elif bad == "blank_comment":
        payload["comment"] = " \n "
    elif bad == "adjust":
        payload["action"] = "adjust"
    elif bad == "delta":
        payload["delta"] = "0.01"
    else:
        payload["items"][0]["amount"] = "0.01"
    before = state(submitted)
    response = client.post(PATH, json=payload, headers=csrf(client))
    assert response.status_code == 422, response.text
    assert state(submitted) == before


def test_batch_requires_review_and_read_csrf_and_demo(
    submitted, accounts, report_staff, monkeypatch
):
    operator = login(accounts[0][0], accounts[1])
    association = login(report_staff[0][1], report_staff[1])
    client = login(report_staff[0][0], report_staff[1])
    for denied in [operator, association]:
        assert denied.post(PATH, json=body(submitted), headers=csrf(denied)).status_code == 403
    assert client.post(PATH, json=body(submitted)).status_code == 403
    headers = csrf(client)
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.username == report_staff[0][0]))
        uid = user.id
        profile = RoleProfile(
            name="batch-review-" + secrets.token_hex(8),
            base_role="regulator",
            permissions=["subsidy.read"],
            version=1,
        )
        db.add(profile)
        db.flush()
        pid = profile.id
        user.role_profile_id = pid
    try:
        assert client.post(PATH, json=body(submitted), headers=headers).status_code == 403
        with SessionLocal.begin() as db:
            db.get(RoleProfile, pid).permissions = ["subsidy.review"]
        assert client.post(PATH, json=body(submitted), headers=headers).status_code == 403
        with SessionLocal.begin() as db:
            db.get(RoleProfile, pid).permissions = ["subsidy.read", "subsidy.review"]
        monkeypatch.setattr(settings, "demo_mode", False)
        assert client.post(PATH, json=body(submitted), headers=headers).status_code == 409
        monkeypatch.setattr(settings, "demo_mode", True)
        assert client.post(PATH, json=body(submitted), headers=headers).status_code == 200
    finally:
        with SessionLocal.begin() as db:
            db.get(User, uid).role_profile_id = None
            db.execute(delete(RoleProfile).where(RoleProfile.id == pid))


def test_competing_batches_with_opposite_input_order_commit_only_once(
    submitted, report_staff, monkeypatch
):
    first = login(report_staff[0][0], report_staff[1])
    with SessionLocal.begin() as db:
        original = db.scalar(select(User).where(User.username == report_staff[0][0]))
        user = User(
            username="batch-race-" + secrets.token_hex(8),
            password_hash=original.password_hash,
            role="regulator",
        )
        db.add(user)
        db.flush()
        uid, name = user.id, user.username
    second = login(name, report_staff[1])
    headers = [csrf(first), csrf(second)]
    barrier = Barrier(2)
    original_act = subsidy.act

    def competing(db, user, cid, *args, **kwargs):
        if cid == min(submitted):
            barrier.wait(timeout=10)
        return original_act(db, user, cid, *args, **kwargs)

    monkeypatch.setattr(subsidy, "act", competing)
    try:

        def request(index):
            payload = body(submitted, "approve" if index == 0 else "reject")
            if index:
                payload["items"].reverse()
            return [first, second][index].post(PATH, json=payload, headers=headers[index])

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(request, [0, 1]))
        assert sorted(response.status_code for response in responses) == [200, 409]
        records, allocations, actions = state(submitted)
        assert len({record[1] for record in records}) == 1 and all(
            record[2] == 3 for record in records
        )
        assert allocations == (2 if records[0][1] == "approved" else 0) and actions == 6
        with SessionLocal() as db:
            first_id = db.scalar(select(User.id).where(User.username == report_staff[0][0]))
        assert summary_count(first_id) + summary_count(uid) == 1
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(SubsidyAction).where(SubsidyAction.actor_id == uid))
            db.execute(delete(Audit).where(Audit.user_id == uid))
            db.execute(delete(LoginSession).where(LoginSession.user_id == uid))
            db.execute(delete(User).where(User.id == uid))


def test_batch_accepts_exactly_one_hundred_real_independent_claims(
    submitted, finance, report_staff
):
    uid, station, policy = finance
    ids = list(submitted)
    with SessionLocal.begin() as db:
        user = db.get(User, uid)
        for index in range(98):
            day = date(2026, 1, 1) + timedelta(days=index)
            db.add(
                EnergyRecord(
                    station_id=station,
                    source="batch-limit-test",
                    external_id=secrets.token_hex(12),
                    day=day,
                    kwh="1.000000",
                    eligible=True,
                )
            )
            db.flush()
            claim = sample_claim(db, user, station, policy, day, day)
            subsidy.act(db, user, claim.id, "submit", 1, "")
            ids.append(claim.id)
    client = login(report_staff[0][0], report_staff[1])
    response = client.post(PATH, json=body(ids), headers=csrf(client))
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 100
    rows, allocations, actions = state(ids)
    assert all(row[1:3] == ("approved", 3) for row in rows)
    assert allocations == 100 and actions == 300


def test_batch_audit_projection_keeps_only_bounded_integer_receipts():
    from app.routers.audits import safe_details

    raw = {
        "decision": "approve",
        "count": 1,
        "comment": "private comment",
        "claims": [{"id": 1, "before_version": 2, "after_version": 3, "phone": "private phone"}],
    }
    assert safe_details(raw) == {
        "decision": "approve",
        "count": 1,
        "claims": [{"id": 1, "before_version": 2, "after_version": 3}],
    }
    assert (
        safe_details(
            {
                "decision": {},
                "count": True,
                "claims": [{"id": True, "before_version": 2, "after_version": 3}],
            }
        )
        == {}
    )
    assert safe_details({"claims": raw["claims"] * 101}) == {}
