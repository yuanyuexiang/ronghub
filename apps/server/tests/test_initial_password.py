import json
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.main import app
from app.models import Audit, LoginSession, RoleProfile, Station, User
from app.routers.accounts import AccountInput


@pytest.mark.parametrize("role", ["regulator", "association", "operator"])
def test_admin_created_temporary_password_requires_rotation_for_every_role(accounts, staff, role):
    _, password, stations = accounts
    clients = [
        TestClient(app, client=("initial-password-" + secrets.token_hex(8), 1)) for _ in range(3)
    ]
    admin, first, second = clients
    pid = uid = None
    temporary, replacement = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    username = "initial-" + secrets.token_hex(8)

    def login(client, name, secret):
        result = client.post("/api/v1/auth/login", json={"username": name, "password": secret})
        assert result.status_code == 200, result.text
        return {"x-csrf-token": result.json()["csrf_token"]}

    try:
        headers = login(admin, staff[0][0], password)
        profile = admin.post(
            "/api/v1/roles",
            headers=headers,
            json={"name": username, "base_role": role, "permissions": ["stations.read"]},
        )
        assert profile.status_code == 201, profile.text
        pid = profile.json()["id"]
        with SessionLocal() as db:
            oid = db.get(Station, stations[0]).operator_id
        body = {
            "username": username,
            "password": temporary,
            "role_profile_id": pid,
            "operator_id": oid if role == "operator" else None,
            "must_change_password": False,
        }
        assert temporary not in repr(AccountInput.model_validate(body))
        blank = admin.post("/api/v1/accounts", headers=headers, json={**body, "password": " " * 12})
        assert blank.status_code == 422 and temporary not in blank.text
        created = admin.post("/api/v1/accounts", headers=headers, json=body)
        assert created.status_code == 201, created.text
        uid = created.json()["id"]
        assert created.json()["must_change_password"] is True and created.json()["version"] == 1
        assert temporary not in created.text
        h1 = login(first, username, temporary)
        login(second, username, temporary)
        assert first.get("/api/v1/auth/me").json()["must_change_password"] is True
        for path in [
            "/api/v1/dashboard",
            "/api/v1/stations",
            "/api/v1/auth/phone",
            "/api/v1/content",
        ]:
            assert first.get(path).status_code == 403
        assert (
            first.post(
                "/api/v1/auth/password",
                headers=h1,
                json={"current_password": "wrong", "new_password": replacement},
            ).status_code
            == 403
        )
        assert (
            first.post(
                "/api/v1/auth/password",
                headers=h1,
                json={"current_password": temporary, "new_password": temporary},
            ).status_code
            == 422
        )
        assert first.get("/api/v1/auth/me").json()["must_change_password"] is True
        changed = first.post(
            "/api/v1/auth/password",
            headers=h1,
            json={"current_password": temporary, "new_password": replacement},
        )
        assert changed.status_code == 200 and changed.json()["reauthenticate"]
        assert first.get("/api/v1/auth/me").status_code == 401
        assert second.get("/api/v1/auth/me").status_code == 401
        assert (
            first.post(
                "/api/v1/auth/login", json={"username": username, "password": temporary}
            ).status_code
            == 401
        )
        login(first, username, replacement)
        assert first.get("/api/v1/auth/me").json()["must_change_password"] is False
        assert first.get("/api/v1/stations").status_code == 200
        with SessionLocal() as db:
            assert db.get(User, uid).version == 2
            history = list(
                db.scalars(
                    select(Audit).where(
                        Audit.object_id == uid,
                        Audit.action.in_(["account.create", "password.change"]),
                    )
                )
            )
            assert len(history) == 2
            encoded = json.dumps([row.detail for row in history])
            assert (
                temporary not in encoded
                and replacement not in encoded
                and "password_hash" not in encoded
            )
            assert (
                db.scalar(select(User).where(User.username == accounts[0][0])).must_change_password
                is False
            )
    finally:
        for client in clients:
            client.close()
        with SessionLocal.begin() as db:
            if uid:
                db.execute(
                    delete(Audit).where(
                        (Audit.user_id == uid)
                        | ((Audit.object_id == uid) & (Audit.action == "account.create"))
                    )
                )
                db.execute(delete(LoginSession).where(LoginSession.user_id == uid))
                db.execute(delete(User).where(User.id == uid))
            if pid:
                db.execute(
                    delete(Audit).where(Audit.object_id == pid, Audit.action == "role.create")
                )
                db.execute(delete(RoleProfile).where(RoleProfile.id == pid))
