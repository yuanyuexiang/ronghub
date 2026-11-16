# ruff: noqa: F811 - pytest resolves imported fixtures by parameter name
import csv
import io
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import event
from test_business_exports import login
from test_rating import rating_fixture  # noqa: F401
from test_subsidy import finance  # noqa: F401

from app.db import SessionLocal, engine
from app.models import RatingAction, RatingApplication, SubsidyAction, SubsidyClaim, User


@pytest.fixture
def date_history(accounts, finance, rating_fixture):
    uid, sid, policy_id = finance
    standard_id = rating_fixture[2]
    ids = {"subsidy": [], "ratings": []}
    start = datetime(2026, 11, 14, 16, tzinfo=UTC)  # Beijing Nov 15, 00:00
    with SessionLocal.begin() as db:
        operator_id = db.get(User, uid).operator_id
        for index in range(30):
            state = "draft" if index == 28 else "approved" if index == 29 else "submitted"
            claim = SubsidyClaim(
                station_id=sid,
                operator_id=operator_id,
                policy_id=policy_id,
                start_date=date(2026, 11, 1)
                if index == 1
                else date(2026, 11, 30)
                if index == 2
                else date(2026, 12, 1),
                end_date=date(2026, 11, 30)
                if index == 1
                else date(2026, 12, 1)
                if index == 2
                else date(2026, 12, 31),
                total_kwh="1",
                base_amount="0.10",
                adjusted_amount="0.10",
                snapshot={},
                status=state,
                created_at=datetime(2026, 11, 1, tzinfo=UTC),
            )
            application = RatingApplication(
                station_id=sid,
                operator_id=operator_id,
                standard_id=standard_id,
                initial_scores={},
                current_scores={},
                calculation={},
                rule_snapshot={},
                status="reviewed" if state == "approved" else state,
                created_at=datetime(2026, 11, 1, tzinfo=UTC),
            )
            db.add_all([claim, application])
            db.flush()
            ids["subsidy"].append(claim.id)
            ids["ratings"].append(application.id)
            if index >= 28:
                continue  # Draft and legacy final record have no submission receipt.
            stamp = (
                start - timedelta(microseconds=1)
                if index == 0
                else start + timedelta(days=1)
                if index == 26
                else start + timedelta(minutes=index)
            )
            for action, key, value in [
                (SubsidyAction, "claim_id", claim.id),
                (RatingAction, "application_id", application.id),
            ]:
                db.add(
                    action(
                        **{key: value},
                        actor_id=uid,
                        action="submit",
                        comment="historical receipt",
                        created_at=stamp,
                    )
                )
                if index == 27:
                    db.add(
                        action(
                            **{key: value},
                            actor_id=uid,
                            action="submit",
                            comment="resubmitted later",
                            created_at=start + timedelta(days=1, hours=1),
                        )
                    )
    return ids, uid, start


def csv_rows(response):
    assert response.status_code == 200, response.text
    return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))


@pytest.mark.parametrize(
    "module,base",
    [("subsidy", "/api/v1/subsidy/claims"), ("ratings", "/api/v1/ratings/applications")],
)
def test_submission_calendar_boundaries_latest_receipt_and_full_exports(
    date_history, accounts, module, base
):
    ids, _, _ = date_history
    client = login(accounts[0][0], accounts[1])
    foreign = login(accounts[0][1], accounts[1])
    params = {"submitted_from": "2026-11-15", "submitted_to": "2026-11-15"}
    first = client.get(base, params=params).json()
    second = client.get(base, params={**params, "page": 2}).json()
    expected = set(ids[module][1:26])
    assert first["total"] == second["total"] == 25
    assert {row["id"] for row in first["items"] + second["items"]} == expected
    assert all(row["last_submitted_at"] is not None for row in first["items"])
    exported = csv_rows(client.get(base + "/export", params=params))
    key = "申请编号" if module == "subsidy" else "申报编号"
    assert {int(row[key]) for row in exported} == expected
    assert len(csv_rows(client.get(base + "/process/export", params=params))) == 25
    assert foreign.get(base, params=params).json()["total"] == 0
    assert csv_rows(foreign.get(base + "/export", params=params)) == []
    assert client.get(base, params={"submitted_to": "2026-11-14"}).json()["total"] == 1
    assert client.get(base, params={"submitted_from": "2026-11-16"}).json()["total"] == 2
    for legacy in ids[module][28:]:
        assert (
            client.get(base, params={"record_id": legacy}).json()["items"][0]["last_submitted_at"]
            is None
        )
    for invalid in [
        {"submitted_from": "2026-11-16", "submitted_to": "2026-11-15"},
        {"submitted_to": "2026-02-30"},
        {"submitted_to": "9999-12-31"},
        {"period_from": "2026-11-16", "period_to": "2026-11-15"},
    ]:
        for suffix in ["", "/export", "/process/export"]:
            assert client.get(base + suffix, params=invalid).status_code == 422
    if module == "subsidy":
        overlap = {**params, "period_from": "2026-11-30", "period_to": "2026-11-30"}
        assert client.get(base, params=overlap).json()["total"] == 2
        assert len(csv_rows(client.get(base + "/export", params=overlap))) == 2
        assert len(csv_rows(client.get(base + "/process/export", params=overlap))) == 2
        assert (
            client.get(
                base, params={**params, "period_from": "2026-12-01", "period_to": "2026-12-01"}
            ).json()["total"]
            == 24
        )
    else:
        for suffix in ["", "/export", "/process/export"]:
            assert (
                client.get(base + suffix, params={"period_from": "2026-11-01"}).status_code == 422
            )


def test_paged_count_rows_and_submission_time_share_repeatable_snapshot(date_history, accounts):
    ids, uid, start = date_history
    client = login(accounts[0][0], accounts[1])
    changed = False

    def concurrent_resubmit(connection, cursor, statement, parameters, context, executemany):
        nonlocal changed
        if not changed and "count(*)" in statement and "subsidy_claims" in statement:
            changed = True
            with SessionLocal.begin() as other:
                other.add(
                    SubsidyAction(
                        claim_id=ids["subsidy"][25],
                        actor_id=uid,
                        action="submit",
                        comment="concurrent resubmit",
                        created_at=start + timedelta(days=1),
                    )
                )

    event.listen(engine, "after_cursor_execute", concurrent_resubmit)
    try:
        params = {"submitted_from": "2026-11-15", "submitted_to": "2026-11-15", "page_size": 100}
        response = client.get("/api/v1/subsidy/claims", params=params)
    finally:
        event.remove(engine, "after_cursor_execute", concurrent_resubmit)
    assert changed and response.status_code == 200
    assert response.json()["total"] == len(response.json()["items"]) == 25
    assert client.get("/api/v1/subsidy/claims", params=params).json()["total"] == 24
