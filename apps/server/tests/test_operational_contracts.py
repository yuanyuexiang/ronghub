import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from alert_notification_samples import remove_for_events
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.db import SessionLocal
from app.main import app
from app.models import (
    AlertCursor,
    AlertEvent,
    AlertNotice,
    AlertRule,
    Audit,
    RoleProfile,
    SyncTask,
    User,
    now,
)
from app.operational_contracts import LegacyRuleCreated, LegacySampleResult, SyncAccepted


@pytest.fixture()
def case(accounts, staff):
    clients = [
        TestClient(
            app, client=("operational-" + secrets.token_hex(8), 1), raise_server_exceptions=False
        )
        for _ in range(3)
    ]
    headers = []
    for c, name in zip(clients, [staff[0][0], staff[0][0], accounts[0][0]]):
        response = c.post("/api/v1/auth/login", json={"username": name, "password": accounts[1]})
        assert response.status_code == 200
        headers.append({"x-csrf-token": response.json()["csrf_token"]})
    with SessionLocal() as db:
        opids = [
            db.scalar(select(User.operator_id).where(User.username == name)) for name in accounts[0]
        ]
    code = "operational-" + secrets.token_hex(12)
    body = {
        "code": code,
        "version": 1,
        "name": "Legacy operational fixture",
        "metric": "offline_minutes",
        "unit": "分钟",
        "trigger_value": "30",
        "recover_value": "5",
    }
    rule = clients[0].post("/api/v1/alerts/rules", headers=headers[0], json=body)
    assert rule.status_code == 201, rule.text
    rid = rule.json()["id"]
    try:
        yield {
            "clients": clients,
            "headers": headers,
            "opids": opids,
            "body": body,
            "rule": rid,
            "station": accounts[2][0],
            "username": staff[0][0],
        }
    finally:
        for c in clients:
            c.close()
        with SessionLocal.begin() as db:
            tasks = select(SyncTask.id).where(SyncTask.operator_id.in_(opids))
            db.execute(
                delete(Audit).where(Audit.action == "sync.enqueue", Audit.object_id.in_(tasks))
            )
            db.execute(delete(SyncTask).where(SyncTask.operator_id.in_(opids)))
            ids = select(AlertRule.id).where(AlertRule.code == code)
            events = select(AlertEvent.id).where(AlertEvent.rule_id.in_(ids))
            db.execute(delete(AlertCursor).where(AlertCursor.rule_id.in_(ids)))
            remove_for_events(db, events)
            db.execute(delete(AlertNotice).where(AlertNotice.event_id.in_(events)))
            db.execute(delete(AlertEvent).where(AlertEvent.rule_id.in_(ids)))
            db.execute(delete(AlertRule).where(AlertRule.code == code))


def fail(value):
    raise RuntimeError("Projection rejected before commit")


def test_sync_selector_requires_own_capability_and_global_scope(case):
    c = case
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.username == c["username"]))
        old_profile, old_districts = user.role_profile_id, user.allowed_districts
        profile = RoleProfile(
            name="sync-only-" + secrets.token_hex(12),
            base_role="regulator",
            permissions=["sync.read", "sync.write"],
        )
        db.add(profile)
        db.flush()
        profile_id = profile.id
        user.role_profile_id = profile_id
    try:
        client = c["clients"][0]
        assert client.get("/api/v1/operators").status_code == 403
        response = client.get("/api/v1/sync/operators")
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert all(set(item) == {"id", "name"} for item in response.json()["items"])
        assert c["opids"][0] in [item["id"] for item in response.json()["items"]]
        assert (
            client.post(
                "/api/v1/sync/tasks",
                headers=c["headers"][0],
                json={"operator_id": c["opids"][0], "request_key": secrets.token_hex(16)},
            ).status_code
            == 202
        )
        for districts in [[], ["测试"]]:
            with SessionLocal.begin() as db:
                db.scalar(
                    select(User).where(User.username == c["username"])
                ).allowed_districts = districts
            assert client.get("/api/v1/sync/operators").status_code == 403
        assert c["clients"][2].get("/api/v1/sync/operators").status_code == 403
    finally:
        with SessionLocal.begin() as db:
            user = db.scalar(select(User).where(User.username == c["username"]))
            user.role_profile_id, user.allowed_districts = old_profile, old_districts
            db.flush()
            db.execute(delete(RoleProfile).where(RoleProfile.id == profile_id))


def test_enqueue_projection_failure_has_no_task_or_audit(case, monkeypatch):
    c = case
    key = secrets.token_hex(16)
    with monkeypatch.context() as patch:
        patch.setattr(SyncAccepted, "model_validate", fail)
        result = c["clients"][0].post(
            "/api/v1/sync/tasks",
            headers=c["headers"][0],
            json={"operator_id": c["opids"][0], "request_key": key},
        )
        assert result.status_code == 500
    with SessionLocal() as db:
        assert db.scalar(select(SyncTask.id).where(SyncTask.request_key == key)) is None
        uid = db.scalar(select(User.id).where(User.username == c["username"]))
        assert (
            db.scalar(select(Audit.id).where(Audit.user_id == uid, Audit.action == "sync.enqueue"))
            is None
        )


def test_concurrent_identical_enqueue_creates_one_task_one_audit_and_lookup(case):
    c = case
    body = {"operator_id": c["opids"][0], "request_key": secrets.token_hex(16)}

    def send(index):
        return c["clients"][index].post(
            "/api/v1/sync/tasks", headers=c["headers"][index], json=body
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(send, [0, 1]))
    assert all(r.status_code == 202 for r in responses)
    assert responses[0].json()["id"] == responses[1].json()["id"]
    tid = responses[0].json()["id"]
    assert (
        c["clients"][0]
        .post(
            "/api/v1/sync/tasks",
            headers=c["headers"][0],
            json={**body, "operator_id": c["opids"][1]},
        )
        .status_code
        == 409
    )
    result = c["clients"][0].get("/api/v1/sync/tasks", params={"request_key": body["request_key"]})
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    assert [r["id"] for r in result.json()["items"]] == [tid]
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(Audit)
                .where(Audit.action == "sync.enqueue", Audit.object_id == tid)
            )
            == 1
        )


def test_legacy_result_null_and_empty_preserved_without_worker_secrets(case):
    c = case
    keys = [secrets.token_hex(16) for _ in range(2)]
    with SessionLocal.begin() as db:
        for key, result in zip(keys, [None, {}]):
            db.add(
                SyncTask(
                    source="demo",
                    request_key=key,
                    operator_id=c["opids"][0],
                    status="succeeded",
                    result=result,
                    lease_owner="private-lease-never-returned",
                )
            )
    for key, expected in zip(keys, [None, {}]):
        response = c["clients"][0].get("/api/v1/sync/tasks", params={"request_key": key})
        assert response.status_code == 200, response.text
        assert response.json()["items"][0]["result"] == expected
        assert "private-lease" not in response.text and "lease_owner" not in response.text
    assert c["clients"][2].get("/api/v1/sync/tasks").status_code == 403


def test_rule_and_sample_projection_roll_back_creation_and_event_cursor_notices(case, monkeypatch):
    c = case
    with monkeypatch.context() as patch:
        patch.setattr(LegacyRuleCreated, "model_validate", fail)
        result = c["clients"][0].post(
            "/api/v1/alerts/rules", headers=c["headers"][0], json={**c["body"], "version": 2}
        )
        assert result.status_code == 500
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(AlertRule.id).where(
                    AlertRule.code == c["body"]["code"], AlertRule.version == 2
                )
            )
            is None
        )
    body = {
        "station_id": c["station"],
        "rule_id": c["rule"],
        "target": "owned-test-target",
        "value": "40",
        "observed_at": (now() - timedelta(minutes=1)).isoformat(),
    }
    with monkeypatch.context() as patch:
        patch.setattr(LegacySampleResult, "model_validate", fail)
        assert (
            c["clients"][0]
            .post("/api/v1/alerts/samples", headers=c["headers"][0], json=body)
            .status_code
            == 500
        )
    with SessionLocal() as db:
        assert db.scalar(select(AlertEvent.id).where(AlertEvent.rule_id == c["rule"])) is None
        assert db.scalar(select(AlertCursor.id).where(AlertCursor.rule_id == c["rule"])) is None
    first = c["clients"][0].post("/api/v1/alerts/samples", headers=c["headers"][0], json=body)
    assert first.status_code == 200 and first.json()["outcome"] == "opened"
    repeated = c["clients"][0].post("/api/v1/alerts/samples", headers=c["headers"][0], json=body)
    assert (
        repeated.json()["outcome"] == "ignored_stale"
        and repeated.json()["event_id"] == first.json()["event_id"]
    )
    assert first.headers["cache-control"] == "no-store"
    assert c["clients"][0].get("/api/v1/alerts/rules").headers["cache-control"] == "no-store"
    for stamp in ["2026-01-01T00:00:00", "0001-01-01T00:00:00+14:00"]:
        assert (
            c["clients"][0]
            .post(
                "/api/v1/alerts/samples",
                headers=c["headers"][0],
                json={**body, "observed_at": stamp},
            )
            .status_code
            == 422
        )
