import json
import secrets
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.dependencies import token_hash
from app.main import app
from app.models import Audit, EnrollmentSession, Registration, User, now


def login(client, name, password):
    response = client.post("/api/v1/auth/login", json={"username": name, "password": password})
    assert response.status_code == 200
    return {"x-csrf-token": response.json()["csrf_token"]}


@pytest.fixture()
def linked_registration(accounts):
    names, _, _ = accounts
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.username == names[0]))
        registration = Registration(
            username=names[0],
            password_hash=user.password_hash,
            company_name="password-test-" + secrets.token_hex(8),
            credit_code="91350100" + secrets.token_hex(5).upper(),
            registration_address="test",
            contacts=[],
            status="approved",
            user_id=user.id,
            operator_id=user.operator_id,
        )
        db.add(registration)
        db.flush()
        rid = registration.id
        token = secrets.token_urlsafe(32)
        db.add(
            EnrollmentSession(
                token_hash=token_hash(token),
                registration_id=rid,
                csrf=secrets.token_urlsafe(24),
                expires_at=now() + timedelta(hours=1),
            )
        )
    yield rid, token
    with SessionLocal.begin() as db:
        db.execute(delete(EnrollmentSession).where(EnrollmentSession.registration_id == rid))
        db.execute(delete(Registration).where(Registration.id == rid))


def test_self_password_change_revokes_sessions_and_never_echoes_secrets(accounts):
    names, password, _ = accounts
    replacement = secrets.token_urlsafe(24)
    with (
        TestClient(app, client=("password-a-" + secrets.token_hex(4), 1)) as first,
        TestClient(app, client=("password-b-" + secrets.token_hex(4), 1)) as second,
    ):
        headers = login(first, names[0], password)
        login(second, names[0], password)
        url = "/api/v1/auth/password"
        malformed = first.post(
            url,
            headers=headers,
            json={"current_password": password, "new_password": "secret-short"[:8]},
        )
        assert malformed.status_code == 422
        assert password not in malformed.text and "secret-s" not in malformed.text
        wrong = first.post(
            url,
            headers=headers,
            json={"current_password": "wrong-password", "new_password": replacement},
        )
        assert wrong.status_code == 403
        assert first.get("/api/v1/auth/me").status_code == 200
        assert (
            first.post(
                url, headers=headers, json={"current_password": password, "new_password": password}
            ).status_code
            == 422
        )
        changed = first.post(
            url, headers=headers, json={"current_password": password, "new_password": replacement}
        )
        assert changed.status_code == 200 and changed.json()["reauthenticate"]
        assert first.get("/api/v1/auth/me").status_code == 401
        assert second.get("/api/v1/auth/me").status_code == 401
        assert (
            first.post(
                "/api/v1/auth/login", json={"username": names[0], "password": password}
            ).status_code
            == 401
        )
        login(first, names[0], replacement)
        assert first.get("/api/v1/stations").status_code == 200
        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.username == names[0]))
            logs = list(
                db.scalars(
                    select(Audit).where(
                        Audit.object_id == user.id, Audit.action == "password.change"
                    )
                )
            )
            assert len(logs) == 1
            encoded = json.dumps(logs[0].detail)
            assert (
                password not in encoded
                and replacement not in encoded
                and user.password_hash not in encoded
            )


def test_admin_recovery_is_scoped_and_requires_temporary_password_change(
    accounts, staff, linked_registration
):
    names, password, _ = accounts
    staffnames, _ = staff
    temporary = secrets.token_urlsafe(24)
    permanent = secrets.token_urlsafe(24)
    with (
        TestClient(app, client=("reset-admin-" + secrets.token_hex(4), 1)) as admin,
        TestClient(app, client=("reset-op-" + secrets.token_hex(4), 1)) as owner,
    ):
        admin_headers = login(admin, staffnames[0], password)
        login(owner, names[0], password)
        for _ in range(5):
            assert (
                owner.post(
                    "/api/v1/auth/login",
                    json={"username": names[0], "password": "wrong-before-recovery"},
                ).status_code
                == 401
            )
        assert (
            owner.post(
                "/api/v1/auth/login", json={"username": names[0], "password": password}
            ).status_code
            == 429
        )
        with SessionLocal.begin() as db:
            target = db.scalar(select(User).where(User.username == names[0]))
            target.allowed_districts = ["测试"]
            tid, version = target.id, target.version
            issuer = db.scalar(select(User).where(User.username == staffnames[0]))
            issuer.allowed_districts = ["不匹配"]
        url = f"/api/v1/accounts/{tid}/password-reset"
        body = {
            "admin_password": password,
            "temporary_password": temporary,
            "version": version,
            "reason": "已线下核验账号持有人",
        }
        assert admin.post(url, headers=admin_headers, json=body).status_code == 404
        with SessionLocal.begin() as db:
            db.scalar(select(User).where(User.username == staffnames[0])).allowed_districts = [
                "测试"
            ]
        assert (
            admin.post(
                url, headers=admin_headers, json={**body, "admin_password": "wrong-admin"}
            ).status_code
            == 403
        )
        assert (
            admin.post(url, headers=admin_headers, json={**body, "version": 999}).status_code == 409
        )
        reset = admin.post(url, headers=admin_headers, json=body)
        assert reset.status_code == 200 and reset.json()["must_change_password"]
        with TestClient(app, client=("recovery-progress-" + secrets.token_hex(4), 1)) as progress:
            progress.cookies.set(
                "ronghub_enrollment", linked_registration[1], path="/api/v1/enrollment"
            )
            assert progress.get("/api/v1/enrollment/me").status_code == 401
            assert (
                progress.post(
                    "/api/v1/enrollment/login", json={"username": names[0], "password": temporary}
                ).status_code
                == 403
            )
        assert owner.get("/api/v1/auth/me").status_code == 401
        assert (
            owner.post(
                "/api/v1/auth/login", json={"username": names[0], "password": password}
            ).status_code
            == 401
        )
        temporary_headers = login(owner, names[0], temporary)
        assert owner.get("/api/v1/auth/me").json()["must_change_password"] is True
        assert owner.get("/api/v1/stations").status_code == 403
        changed = owner.post(
            "/api/v1/auth/password",
            headers=temporary_headers,
            json={"current_password": temporary, "new_password": permanent},
        )
        assert changed.status_code == 200
        login(owner, names[0], permanent)
        assert owner.get("/api/v1/auth/me").json()["must_change_password"] is False
        assert owner.get("/api/v1/stations").status_code == 200
        with TestClient(app, client=("changed-progress-" + secrets.token_hex(4), 1)) as progress:
            assert (
                progress.post(
                    "/api/v1/enrollment/login", json={"username": names[0], "password": permanent}
                ).status_code
                == 200
            )
            assert progress.get("/api/v1/enrollment/me").status_code == 200
        with SessionLocal() as db:
            log = db.scalar(
                select(Audit).where(Audit.action == "password.reset", Audit.object_id == tid)
            )
            assert temporary not in json.dumps(log.detail)


def test_password_verification_throttling_is_persistent(accounts):
    names, password, _ = accounts
    with TestClient(app, client=("password-limit-" + secrets.token_hex(4), 1)) as client:
        headers = login(client, names[0], password)
        for _ in range(5):
            assert (
                client.post(
                    "/api/v1/auth/password",
                    headers=headers,
                    json={
                        "current_password": "wrong-current",
                        "new_password": secrets.token_urlsafe(24),
                    },
                ).status_code
                == 403
            )
        response = client.post(
            "/api/v1/auth/password",
            headers=headers,
            json={"current_password": password, "new_password": secrets.token_urlsafe(24)},
        )
        assert response.status_code == 429 and response.headers["retry-after"] == "900"
