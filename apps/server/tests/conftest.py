import secrets

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.main import passwords
from app.models import (
    ApprovalAction,
    Attachment,
    Audit,
    ChargingOrder,
    FaultEvent,
    HourlyEnergyRecord,
    LoginSession,
    Operator,
    RuntimeInterval,
    Station,
    StationApplication,
    User,
)


@pytest.fixture()
def accounts():
    suffix = secrets.token_hex(6)
    password = secrets.token_urlsafe(18)
    ids, names, station_ids = [], [], []
    with SessionLocal.begin() as db:
        for i in range(2):
            op = Operator(name=f"test-{suffix}-{i}")
            db.add(op)
            db.flush()
            u = User(
                username=f"test-{suffix}-{i}",
                password_hash=passwords.hash(password),
                role="operator",
                operator_id=op.id,
            )
            db.add(u)
            db.flush()
            st = Station(
                operator_id=op.id,
                source="test",
                external_id=f"{suffix}-{i}",
                name="隔离测试",
                district="测试",
                address="测试",
                power_kw=120,
                connectors=2,
            )
            db.add(st)
            db.flush()
            ids.append((op.id, u.id))
            names.append(u.username)
            station_ids.append(st.id)
    yield names, password, station_ids
    with SessionLocal.begin() as db:
        db.execute(delete(Audit).where(Audit.user_id.in_([u for _, u in ids])))
        db.execute(delete(LoginSession).where(LoginSession.user_id.in_([u for _, u in ids])))
        app_ids = select(StationApplication.id).where(
            StationApplication.station_id.in_(station_ids)
        )
        db.execute(delete(Attachment).where(Attachment.application_id.in_(app_ids)))
        db.execute(delete(ApprovalAction).where(ApprovalAction.application_id.in_(app_ids)))
        db.execute(delete(StationApplication).where(StationApplication.station_id.in_(station_ids)))
        db.execute(delete(RuntimeInterval).where(RuntimeInterval.station_id.in_(station_ids)))
        db.execute(delete(FaultEvent).where(FaultEvent.station_id.in_(station_ids)))
        db.execute(delete(HourlyEnergyRecord).where(HourlyEnergyRecord.station_id.in_(station_ids)))
        db.execute(delete(ChargingOrder).where(ChargingOrder.station_id.in_(station_ids)))
        db.execute(delete(Station).where(Station.id.in_(station_ids)))
        db.execute(delete(User).where(User.id.in_([u for _, u in ids])))
        db.execute(delete(Operator).where(Operator.id.in_([o for o, _ in ids])))


@pytest.fixture()
def staff(accounts):
    names, password, _ = accounts
    created = []
    with SessionLocal.begin() as db:
        owner = db.scalar(select(User).where(User.username == names[0]))
        for role in ["regulator", "association"]:
            user = User(
                username=role + "-" + secrets.token_hex(8),
                password_hash=owner.password_hash,
                role=role,
            )
            db.add(user)
            db.flush()
            created.append((user.id, user.username))
    yield [name for _, name in created], password
    with SessionLocal.begin() as db:
        uids = [uid for uid, _ in created]
        db.execute(delete(Audit).where(Audit.user_id.in_(uids)))
        db.execute(delete(LoginSession).where(LoginSession.user_id.in_(uids)))
        db.execute(delete(User).where(User.id.in_(uids)))


@pytest.fixture(autouse=True)
def real_login_challenges_for_existing_scenarios(monkeypatch, request):
    """Seed known image text in-process; every scenario still uses the real issue/consume API."""
    if request.node.get_closest_marker("captcha_manual"):
        return
    from fastapi.testclient import TestClient

    from app import captcha

    with SessionLocal.begin() as db:
        db.execute(
            delete(captcha.LoginThrottle).where(
                captcha.LoginThrottle.key.in_(
                    [
                        captcha.token_hash("captcha:" + phase + ":testclient")
                        for phase in ("issue", "verify")
                    ]
                )
            )
        )
    monkeypatch.setattr(captcha, "generate_answer", lambda: "ABCDE")
    original = TestClient.post

    def with_challenge(client, url, *args, **kwargs):
        body = kwargs.get("json")
        if (
            str(url).endswith("/api/v1/auth/login")
            and isinstance(body, dict)
            and "captcha_id" not in body
        ):
            issued = client.get("/api/v1/auth/captcha")
            assert issued.status_code == 200, issued.text
            kwargs["json"] = {
                **body,
                "captcha_id": issued.json()["captcha_id"],
                "captcha_answer": "ABCDE",
            }
        return original(client, url, *args, **kwargs)

    monkeypatch.setattr(TestClient, "post", with_challenge)


@pytest.fixture()
def business_login():
    """Explicitly complete first-login rotation for business permission scenarios.

    Security-policy tests use raw login so they still observe the restricted state.
    This fixture uses public endpoints, never rewrites password hashes or flags.
    """
    credentials = {}

    def ready(client, username, initial_password):
        current = credentials.get(username, initial_password)
        result = client.post("/api/v1/auth/login", json={"username": username, "password": current})
        assert result.status_code == 200, result.text
        state = client.get("/api/v1/auth/me")
        assert state.status_code == 200
        if state.json()["must_change_password"]:
            assert client.get("/api/v1/stations").status_code == 403
            replacement = secrets.token_urlsafe(24)
            changed = client.post(
                "/api/v1/auth/password",
                headers={"x-csrf-token": result.json()["csrf_token"]},
                json={"current_password": current, "new_password": replacement},
            )
            assert changed.status_code == 200, changed.text
            assert client.get("/api/v1/auth/me").status_code == 401
            credentials[username] = replacement
            result = client.post(
                "/api/v1/auth/login", json={"username": username, "password": replacement}
            )
            assert result.status_code == 200, result.text
        return result

    return ready
