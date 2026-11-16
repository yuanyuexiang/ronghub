# ruff: noqa: F811 - pytest fixture parameters shadow fixture imports
from datetime import timedelta

import pytest
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select
from test_business_exports import login, report_staff  # noqa: F401
from test_rating import rating_fixture  # noqa: F401
from test_subsidy import finance  # noqa: F401

from app import rating, rating_results, subsidy
from app.business_contracts import DecimalText
from app.db import SessionLocal
from app.models import (
    Audit,
    EnergyAllocation,
    RatingAction,
    RatingApplication,
    RatingStandard,
    SubsidyAction,
    SubsidyClaim,
    User,
)


def headers(client):
    return {"x-csrf-token": client.get("/api/v1/auth/me").json()["csrf_token"]}


def claim_body(finance):
    return {
        "station_id": finance[1],
        "policy_id": finance[2],
        "start_date": "2026-11-01",
        "end_date": "2026-11-02",
    }


def test_decimal_contract_rejects_numeric_and_nonfinite_values():
    adapter = TypeAdapter(DecimalText)
    for invalid in [1, 1.25, True, "NaN", "Infinity", "-Infinity", "", "1.2x"]:
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)
    for valid in ["0.000000", "3.02", "-0.02", "1E-6"]:
        assert adapter.validate_python(valid) == valid


@pytest.mark.parametrize("stage", ["create", "submit"])
def test_claim_projection_failure_rolls_back_business_history_and_allocation(
    finance, accounts, monkeypatch, stage
):
    client = login(accounts[0][0], accounts[1])
    csrf = headers(client)
    cid = None
    if stage == "submit":
        response = client.post("/api/v1/subsidy/claims", json=claim_body(finance), headers=csrf)
        assert response.status_code == 201
        cid = response.json()["id"]
        from subsidy_samples import evidence

        with SessionLocal.begin() as db:
            evidence(db, db.get(SubsidyClaim, cid), db.get(User, finance[0]))

    original = subsidy.output
    monkeypatch.setattr(subsidy, "output", lambda record: {**original(record), "base_amount": 3.02})
    with pytest.raises(ValidationError):
        if cid:
            client.post(
                f"/api/v1/subsidy/claims/{cid}/actions",
                json={"action": "submit", "version": 1},
                headers=csrf,
            )
        else:
            client.post("/api/v1/subsidy/claims", json=claim_body(finance), headers=csrf)
    with SessionLocal() as db:
        if cid:
            claim = db.get(SubsidyClaim, cid)
            assert claim.status == "draft" and claim.version == 1
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(SubsidyAction)
                    .where(SubsidyAction.claim_id == cid)
                )
                == 1
            )
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(EnergyAllocation)
                    .where(EnergyAllocation.claim_id == cid)
                )
                == 2
            )
        else:
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(SubsidyClaim)
                    .where(SubsidyClaim.policy_id == finance[2])
                )
                == 0
            )
        assert (
            db.scalar(
                select(func.count())
                .select_from(Audit)
                .where(Audit.user_id == finance[0], Audit.action == "subsidy.submit")
            )
            == 0
        )


@pytest.mark.parametrize("stage", ["create", "review", "publish"])
def test_rating_projection_failure_cannot_leave_review_or_frozen_publication(
    rating_fixture, accounts, report_staff, monkeypatch, stage
):
    _uid, sid, standard_id = rating_fixture
    owner = login(accounts[0][0], accounts[1])
    reviewer = login(report_staff[0][1], accounts[1])
    oh, rh = headers(owner), headers(reviewer)
    body = {
        "station_id": sid,
        "standard_id": standard_id,
        "scores": {"service": "90"},
        "evidence_note": "fixture",
    }
    aid = None
    if stage != "create":
        created = owner.post("/api/v1/ratings/applications", json=body, headers=oh)
        assert created.status_code == 201
        aid = created.json()["id"]
        assert (
            owner.post(
                f"/api/v1/ratings/applications/{aid}/actions",
                json={"action": "submit", "version": 1},
                headers=oh,
            ).status_code
            == 200
        )
        if stage == "publish":
            assert (
                reviewer.post(
                    f"/api/v1/ratings/applications/{aid}/actions",
                    json={
                        "action": "review",
                        "version": 2,
                        "comment": "fixture review",
                        "scores": {"runtime": "80"},
                    },
                    headers=rh,
                ).status_code
                == 200
            )
    original = rating.output
    monkeypatch.setattr(
        rating,
        "output",
        lambda *args, **kwargs: {**original(*args, **kwargs), "calculation": {"total": 86.0}},
    )
    with pytest.raises(ValidationError):
        if stage == "create":
            owner.post("/api/v1/ratings/applications", json=body, headers=oh)
        else:
            action = {
                "action": stage,
                "version": 2 if stage == "review" else 3,
                "comment": "fixture",
                "scores": {"runtime": "80"} if stage == "review" else {},
            }
            if stage == "publish":
                action["valid_until"] = (
                    rating_results.local_day() + timedelta(days=30)
                ).isoformat()
            reviewer.post(f"/api/v1/ratings/applications/{aid}/actions", json=action, headers=rh)
    with SessionLocal() as db:
        if aid is None:
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(RatingApplication)
                    .where(RatingApplication.standard_id == standard_id)
                )
                == 0
            )
        else:
            application = db.get(RatingApplication, aid)
            assert application.status == ("submitted" if stage == "review" else "reviewed")
            assert application.version == (2 if stage == "review" else 3)
            assert application.report_snapshot is None and application.valid_until is None
            assert db.scalar(
                select(func.count())
                .select_from(RatingAction)
                .where(RatingAction.application_id == aid)
            ) == (2 if stage == "review" else 3)


def test_legacy_missing_detail_remains_unknown_and_real_amounts_stay_strings(
    finance, rating_fixture, accounts
):
    client = login(accounts[0][0], accounts[1])
    csrf = headers(client)
    claim = client.post("/api/v1/subsidy/claims", json=claim_body(finance), headers=csrf)
    assert claim.status_code == 201 and claim.json()["base_amount"] == "3.02"
    cid = claim.json()["id"]
    detail = client.get(f"/api/v1/subsidy/claims/{cid}").json()
    assert detail["snapshot"]["details"][0]["amount"] == "1.01"
    with SessionLocal.begin() as db:
        db.get(SubsidyClaim, cid).snapshot = {}
    legacy = client.get(f"/api/v1/subsidy/claims/{cid}")
    assert legacy.status_code == 200 and legacy.json()["snapshot"]["details"] is None
    with SessionLocal.begin() as db:
        owner = db.get(User, finance[0])
        application = rating.create(
            db, owner, rating_fixture[1], rating_fixture[2], {}, "legacy fixture"
        )
        application.rule_snapshot = {}
        application.before_result = {}
        application.calculation = {"details": [{"code": "legacy", "name": "Legacy", "score": None}]}
        aid = application.id
    legacy_rating = client.get(f"/api/v1/ratings/applications/{aid}")
    assert legacy_rating.status_code == 200
    data = legacy_rating.json()
    assert data["rule_snapshot"]["indicators"] is None and data["before_result"]["id"] is None
    assert data["calculation"]["total"] is None
    assert data["calculation"]["details"][0]["category"] is None
    assert data["calculation"]["details"][0]["source"] is None
    with SessionLocal.begin() as db:
        db.get(RatingStandard, rating_fixture[2]).rules = {}
    standard_list = client.get("/api/v1/ratings/standards")
    assert standard_list.status_code == 200
    assert (
        next(row for row in standard_list.json()["items"] if row["id"] == rating_fixture[2])[
            "rules"
        ]["indicators"]
        is None
    )
    unavailable = client.post(
        "/api/v1/ratings/applications",
        json={"station_id": rating_fixture[1], "standard_id": rating_fixture[2], "scores": {}},
        headers=csrf,
    )
    assert unavailable.status_code == 409 and "历史标准明细未留存" in unavailable.json()["detail"]
    policies = client.get("/api/v1/subsidy/policies")
    assert policies.status_code == 200
    assert all(isinstance(row["rate"], str) for row in policies.json()["items"])
    energy = client.get("/api/v1/subsidy/energy", params={"station_id": finance[1]})
    assert energy.status_code == 200 and all(
        isinstance(row["kwh"], str) for row in energy.json()["items"]
    )
