import secrets
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.main import app
from app.models import AlertEvent, AlertRule, Station, User


def test_alert_queue_independent_states_time_and_scope(accounts, staff):
    names, password, stations = accounts
    at = datetime(2026, 11, 1, tzinfo=UTC)
    ids = []
    with SessionLocal.begin() as db:
        owners = [db.scalar(select(User).where(User.username == name)) for name in names]
        reg_id = db.scalar(select(User.id).where(User.username == staff[0][0]))
        db.get(Station, stations[0]).district = "本区"
        db.get(Station, stations[1]).district = "其他区"
        rule = AlertRule(
            code="queue-" + secrets.token_hex(8),
            version=1,
            name="演示",
            metric="offline",
            unit="min",
            trigger_value=30,
            recover_value=5,
        )
        db.add(rule)
        db.flush()
        rid = rule.id
        snapshot = {
            "code": rule.code,
            "version": 1,
            "metric": "offline",
            "unit": "min",
            "trigger": "30",
            "recover": "5",
            "notifications_enabled": True,
        }
        for index in range(126):
            owner = owners[1] if index == 125 else owners[0]
            status = "recovered" if index in {123, 124} else "active"
            confirmed = index in {122, 124}
            opened = at if index < 122 else at + timedelta(days=1)
            event = AlertEvent(
                rule_id=rid,
                station_id=stations[1] if index == 125 else stations[0],
                operator_id=owner.operator_id,
                target="queue%_" + names[0],
                status=status,
                opened_at=opened,
                last_observed_at=opened,
                recovered_at=opened if status == "recovered" else None,
                last_value="40.123456",
                rule_snapshot=snapshot,
                acknowledged_at=opened if confirmed else None,
                acknowledged_by=reg_id if confirmed else None,
            )
            db.add(event)
            db.flush()
            ids.append(event.id)
    clients = [TestClient(app, client=("alert-queue-" + secrets.token_hex(8), 1)) for _ in range(3)]
    try:
        for client, name in zip(clients, [names[0], staff[0][0], staff[0][1]]):
            assert (
                client.post(
                    "/api/v1/auth/login", json={"username": name, "password": password}
                ).status_code
                == 200
            )
        owner, reg, association = clients
        url = "/api/v1/alerts"
        collected = []
        filters = {
            "q": "%_" + names[0],
            "status": "active",
            "acknowledged": False,
            "rule_id": rid,
            "start_at": at.isoformat(),
            "end_at": (at + timedelta(days=1)).isoformat(),
        }
        for page in range(1, 8):
            result = owner.get(url, params={**filters, "page": page}).json()
            assert result["total"] == 122
            assert all(
                row["last_value"] == "40.123456" and row["rule_snapshot"]["notifications_enabled"]
                for row in result["items"]
            )
            collected.extend(row["id"] for row in result["items"])
        assert collected == list(reversed(ids[:122]))
        for state, confirmed, total in [
            ("active", True, 1),
            ("recovered", False, 1),
            ("recovered", True, 1),
        ]:
            assert (
                owner.get(url, params={"status": state, "acknowledged": confirmed}).json()["total"]
                == total
            )
        assert owner.get(url, params={"record_id": ids[-1]}).json()["total"] == 0
        assert association.get(url).status_code == 403
        for invalid in [
            {"status": "confirmed"},
            {"acknowledged": "bad"},
            {"page_size": 101},
            {"page": 0},
            {"record_id": 2**63},
            {"start_at": "2026-11-01T00:00:00"},
            {"start_at": at.isoformat(), "end_at": at.isoformat()},
        ]:
            assert owner.get(url, params=invalid).status_code == 422
        with SessionLocal.begin() as db:
            db.get(User, reg_id).allowed_districts = ["本区"]
        assert reg.get(url, params={"q": names[0]}).json()["total"] == 125
    finally:
        for client in clients:
            client.close()
        with SessionLocal.begin() as db:
            db.get(User, reg_id).allowed_districts = None
            db.execute(delete(AlertEvent).where(AlertEvent.rule_id == rid))
            db.execute(delete(AlertRule).where(AlertRule.id == rid))
