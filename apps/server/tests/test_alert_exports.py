import csv
import io
import secrets
from datetime import datetime

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from test_configured_alerts import body, submit
from test_configured_alerts import setup as setup  # noqa: PLC0414

from app.db import SessionLocal
from app.main import app
from app.models import Audit, RoleProfile, Station, User
from app.routers.alert_exports import export_window


@pytest.mark.parametrize(
    "start,end,valid",
    [
        ("2026-01-15T00:00:00+08:00", "2026-03-01T00:00:00+08:00", True),
        ("2026-12-15T00:00:00+08:00", "2027-02-01T00:00:00+08:00", True),
        ("2026-01-15T00:00:00+08:00", "2026-03-01T00:00:01+08:00", False),
        ("2026-01-15T00:00:00", "2026-02-01T00:00:00", False),
        ("2026-01-15T00:00:00+08:00", "2026-01-15T00:00:00+08:00", False),
    ],
)
def test_two_natural_month_window(start, end, valid):
    if valid:
        export_window(datetime.fromisoformat(start), datetime.fromisoformat(end))
    else:
        with pytest.raises(HTTPException):
            export_window(datetime.fromisoformat(start), datetime.fromisoformat(end))


def test_export_matches_category_list_and_scopes_without_phone_data(setup, accounts, staff):
    event = submit(setup, body(setup, value="80"))
    with SessionLocal.begin() as db:
        station = db.get(Station, setup["station"])
        station.name = "=IMPORTXML(unsafe)"
    params = {
        "record_id": event.event_id,
        "start_at": "2026-11-01T00:00:00+08:00",
        "end_at": "2026-12-01T00:00:00+08:00",
        "category": "device_charging",
    }
    clients = []
    try:
        for index, username in enumerate(
            [staff[0][0], accounts[0][0], accounts[0][1], staff[0][1]]
        ):
            client = TestClient(app, client=("export-" + secrets.token_hex(6), 1))
            clients.append(client)
            assert (
                client.post(
                    "/api/v1/auth/login", json={"username": username, "password": accounts[1]}
                ).status_code
                == 200
            )
            result = client.get("/api/v1/alerts/export", params=params)
            if index == 3:
                assert result.status_code == 403
                continue
            assert result.status_code == 200, result.text
            assert result.headers["cache-control"] == "no-store"
            rows = list(csv.reader(io.StringIO(result.content.decode("utf-8-sig"))))
            assert len(rows) == (1 if index == 2 else 2)
            assert not any("手机" in header or "号码" in header for header in rows[0])
            listing = client.get("/api/v1/alerts", params=params)
            assert listing.status_code == 200
            assert listing.json()["total"] == len(rows) - 1
            if index != 2:
                assert rows[1][1].startswith("'=") and rows[1][-2] == "manual_demo_v1"
            assert (
                client.get(
                    "/api/v1/alerts/export",
                    params={**params, "end_at": "2027-01-02T00:00:00+08:00"},
                ).status_code
                == 422
            )
            assert (
                client.get(
                    "/api/v1/alerts", params={**params, "category": "device_offline"}
                ).json()["total"]
                == 0
            )
    finally:
        for client in clients:
            client.close()
        with SessionLocal.begin() as db:
            uid = db.scalar(select(User.id).where(User.username == staff[0][0]))
            db.execute(
                delete(Audit).where(
                    Audit.action == "alerts.export",
                    Audit.user_id.in_(
                        [uid, db.scalar(select(User.id).where(User.username == accounts[0][0]))]
                    ),
                )
            )


def test_export_requires_read_and_export_grants_independently(setup, accounts, staff):
    username = staff[0][0]
    with SessionLocal.begin() as db:
        profile = RoleProfile(
            name="export-profile-" + secrets.token_hex(6),
            base_role="regulator",
            permissions=["alerts.read"],
        )
        db.add(profile)
        db.flush()
        pid = profile.id
        user = db.scalar(select(User).where(User.username == username))
        user.role_profile_id = pid
    try:
        with TestClient(app, client=("export-grant-" + secrets.token_hex(6), 1)) as client:
            assert (
                client.post(
                    "/api/v1/auth/login", json={"username": username, "password": accounts[1]}
                ).status_code
                == 200
            )
            assert client.get("/api/v1/alerts").status_code == 200
            assert client.get("/api/v1/alerts/export").status_code == 403
            with SessionLocal.begin() as db:
                db.get(RoleProfile, pid).permissions = ["statistics.export"]
            assert (
                client.get(
                    "/api/v1/alerts/export",
                    params={
                        "start_at": "2026-11-01T00:00:00+08:00",
                        "end_at": "2026-12-01T00:00:00+08:00",
                    },
                ).status_code
                == 403
            )
    finally:
        with SessionLocal.begin() as db:
            user = db.scalar(select(User).where(User.username == username))
            user.role_profile_id = None
            db.flush()
            db.execute(delete(RoleProfile).where(RoleProfile.id == pid))


def test_export_rejects_overflow_instead_of_truncating_and_keeps_frozen_category(
    setup, accounts, staff
):
    from datetime import UTC

    from sqlalchemy import insert

    from app.models import AlertEvent, AlertRule

    stamp = datetime(2026, 11, 1, tzinfo=UTC)
    with SessionLocal.begin() as db:
        station = db.get(Station, setup["station"])
        db.execute(
            insert(AlertEvent),
            [
                {
                    "rule_id": setup["rules"][0],
                    "station_id": station.id,
                    "operator_id": station.operator_id,
                    "target": "bounded-export",
                    "opened_at": stamp,
                    "last_observed_at": stamp,
                    "last_value": "1",
                    "is_demo": True,
                    "rule_snapshot": {
                        "code": "frozen",
                        "trigger": "60",
                        "recover": "50",
                        "version": 1,
                        "metric": "device_temperature",
                        "unit": "℃",
                    },
                }
                for _ in range(10001)
            ],
        )
        db.get(AlertRule, setup["rules"][0]).metric = "changed-current-rule"
    try:
        with TestClient(app, client=("export-cap-" + secrets.token_hex(6), 1)) as client:
            assert (
                client.post(
                    "/api/v1/auth/login", json={"username": staff[0][0], "password": accounts[1]}
                ).status_code
                == 200
            )
            params = {
                "q": "bounded-export",
                "category": "device_charging",
                "start_at": "2026-11-01T00:00:00+08:00",
                "end_at": "2026-12-01T00:00:00+08:00",
            }
            result = client.get("/api/v1/alerts/export", params=params)
            assert result.status_code == 422 and "10000" in result.json()["detail"]
            with SessionLocal.begin() as db:
                first = db.scalar(
                    select(AlertEvent).where(AlertEvent.rule_id == setup["rules"][0]).limit(1)
                )
                db.delete(first)
            result = client.get("/api/v1/alerts/export", params=params)
            assert result.status_code == 200
            rows = list(csv.reader(io.StringIO(result.content.decode("utf-8-sig"))))
            assert len(rows) == 10001
            assert "单位" in rows[0] and "累计异常样本" in rows[0]
            assert rows[1][rows[0].index("单位")] == "℃"
            assert client.get("/api/v1/alerts", params=params).json()["total"] == 10000
    finally:
        with SessionLocal.begin() as db:
            uid = db.scalar(select(User.id).where(User.username == staff[0][0]))
            db.execute(delete(Audit).where(Audit.user_id == uid, Audit.action == "alerts.export"))
