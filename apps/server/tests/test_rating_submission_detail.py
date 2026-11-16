# ruff: noqa: F811 - pytest fixture parameters shadow fixture imports
import secrets
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, event, select
from test_business_exports import login, report_staff  # noqa: F401
from test_rating import rating_fixture  # noqa: F401

from app import rating
from app.db import SessionLocal, engine
from app.models import Audit, LoginSession, Operator, RatingAction, RatingApplication, Station, User


@pytest.fixture
def submitter(rating_fixture):
    uid, _, _ = rating_fixture
    with SessionLocal.begin() as db:
        owner = db.get(User, uid)
        user = User(
            username="submitter-" + secrets.token_hex(8),
            password_hash=owner.password_hash,
            role="operator",
            operator_id=owner.operator_id,
        )
        db.add(user)
        db.flush()
        user_id, name = user.id, user.username
    yield user_id, name
    with SessionLocal.begin() as db:
        db.execute(delete(RatingAction).where(RatingAction.actor_id == user_id))
        db.execute(delete(Audit).where(Audit.user_id == user_id))
        db.execute(delete(LoginSession).where(LoginSession.user_id == user_id))
        db.execute(delete(User).where(User.id == user_id))


def create(rating_fixture):
    uid, station, standard = rating_fixture
    with SessionLocal.begin() as db:
        record = rating.create(
            db, db.get(User, uid), station, standard, {"service": "90"}, "申报提交信息"
        )
        return record.id


def csrf(client):
    return {"x-csrf-token": client.get("/api/v1/auth/me").json()["csrf_token"]}


def test_detail_uses_real_latest_submission_and_current_public_names(
    rating_fixture, accounts, report_staff, submitter
):
    uid, sid, _ = rating_fixture
    aid = create(rating_fixture)
    path = f"/api/v1/ratings/applications/{aid}"
    owner = login(accounts[0][0], accounts[1])
    colleague = login(submitter[1], accounts[1])
    reviewer = login(report_staff[0][1], report_staff[1])
    other = login(accounts[0][1], accounts[1])
    assert other.get(path).status_code == 404
    assert owner.get(path).json()["last_submission"] is None
    assert (
        owner.post(
            path + "/actions", headers=csrf(owner), json={"action": "submit", "version": 1}
        ).status_code
        == 200
    )
    first = owner.get(path).json()["last_submission"]
    assert first["actor_id"] == uid and first["current_account_username"] == accounts[0][0]
    assert (
        reviewer.post(
            path + "/actions",
            headers=csrf(reviewer),
            json={"action": "reject", "version": 2, "comment": "补充材料"},
        ).status_code
        == 200
    )
    assert reviewer.get(path).json()["last_submission"] == first
    assert (
        colleague.post(
            path + "/actions", headers=csrf(colleague), json={"action": "submit", "version": 3}
        ).status_code
        == 200
    )
    stamp = datetime(2026, 11, 14, 16, 0, tzinfo=UTC)
    with SessionLocal.begin() as db:
        latest = db.scalar(
            select(RatingAction)
            .where(RatingAction.application_id == aid, RatingAction.action == "submit")
            .order_by(RatingAction.id.desc())
        )
        latest.created_at = stamp  # Newer action ID can carry an earlier source timestamp.
        action_id = latest.id
        company = db.get(Operator, db.get(User, uid).operator_id)
        company.contacts = [{"name": "PRIVATE-CONTACT-NOT-AN-APPLICANT", "phone": "13800000000"}]
        company.name = "当前企业-" + secrets.token_hex(8)
        db.get(Station, sid).name = "当前场站"
        db.get(User, submitter[0]).enabled = False
        company_name = company.name
    receipt = {
        "action_id": action_id,
        "actor_id": submitter[0],
        "current_account_username": submitter[1],
        "submitted_at": stamp.isoformat(),
    }
    for client in [owner, reviewer, login(report_staff[0][0], report_staff[1])]:
        response = client.get(path)
        assert response.status_code == 200, response.text
        detail = response.json()
        assert detail["station_name"] == "当前场站" and detail["operator_name"] == company_name
        assert detail["last_submission"] == receipt
        assert detail["version"] == 4 and detail["status"] == "submitted"
        assert len(detail["history"]) == 4
        assert "PRIVATE-CONTACT" not in response.text and "13800000000" not in response.text
    queue = owner.get("/api/v1/ratings/applications", params={"record_id": aid}).json()
    assert queue["items"][0]["last_submitted_at"] == receipt["submitted_at"]
    with SessionLocal.begin() as db:
        staff = db.scalar(select(User).where(User.username == report_staff[0][1]))
        staff.allowed_districts = []
    assert reviewer.get(path).status_code == 404


def test_legacy_terminal_record_without_submit_keeps_missing_receipt(rating_fixture, accounts):
    aid = create(rating_fixture)
    with SessionLocal.begin() as db:
        record = db.get(RatingApplication, aid)
        record.status = "reviewed"
        record.version = 7
    client = login(accounts[0][0], accounts[1])
    response = client.get(f"/api/v1/ratings/applications/{aid}")
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["status"] == "reviewed" and detail["last_submission"] is None
    assert detail["created_at"]  # Never used to replace missing submission time/actor.


def test_detail_receipt_names_history_and_state_share_repeatable_snapshot(
    rating_fixture, accounts, submitter
):
    _uid, sid, _ = rating_fixture
    aid = create(rating_fixture)
    client = login(accounts[0][0], accounts[1])
    path = f"/api/v1/ratings/applications/{aid}"
    before = client.get(path).json()
    changed = False
    stamp = datetime(2026, 11, 14, 16, tzinfo=UTC)

    def resubmit(connection, cursor, statement, parameters, context, executemany):
        nonlocal changed
        if not changed and "FROM rating_applications" in statement:
            changed = True
            with SessionLocal.begin() as other:
                record = other.get(RatingApplication, aid)
                record.status = "submitted"
                record.version = 2
                other.get(Station, sid).name = "after-concurrent-submission"
                other.get(Operator, record.operator_id).name = "after-" + secrets.token_hex(8)
                other.add(
                    RatingAction(
                        application_id=aid,
                        actor_id=submitter[0],
                        action="submit",
                        comment="concurrent fixture",
                        created_at=stamp,
                    )
                )

    event.listen(engine, "after_cursor_execute", resubmit)
    try:
        during = client.get(path)
    finally:
        event.remove(engine, "after_cursor_execute", resubmit)
    assert changed and during.status_code == 200, during.text
    assert during.json() == before
    after = client.get(path).json()
    assert after["station_name"] == "after-concurrent-submission"
    assert after["operator_name"] != before["operator_name"]
    assert after["status"] == "submitted" and after["version"] == 2
    assert len(after["history"]) == 2 and len(before["history"]) == 1
    assert before["last_submission"] is None
    assert after["last_submission"]["actor_id"] == submitter[0]
    assert after["last_submission"]["submitted_at"] == stamp.isoformat()
