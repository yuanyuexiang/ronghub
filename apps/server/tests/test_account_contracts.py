import secrets

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, func, select

from app.db import SessionLocal
from app.main import app
from app.models import Audit, RoleProfile, User
from app.routers import accounts as routes


def login(name, password):
    client = TestClient(app, client=("grant-contract-" + secrets.token_hex(6), 1))
    result = client.post("/api/v1/auth/login", json={"username": name, "password": password})
    assert result.status_code == 200, result.text
    return client, {"x-csrf-token": result.json()["csrf_token"]}


@pytest.mark.parametrize(
    "operation", ["role-create", "role-edit", "account-create", "account-edit"]
)
def test_invalid_management_response_rolls_back_grants_accounts_audit_and_revocation(
    accounts, staff, monkeypatch, operation
):
    names, password, _ = accounts
    with SessionLocal.begin() as db:
        profile = RoleProfile(
            name="contract-" + secrets.token_hex(8),
            base_role="operator",
            permissions=["stations.read"],
        )
        db.add(profile)
        db.flush()
        pid = profile.id
        target = db.scalar(select(User).where(User.username == names[0]))
        uid = target.id
        target.role_profile_id = pid
    reg, headers = login(staff[0][0], password)
    owner, _ = login(names[0], password)
    try:
        before = owner.get("/api/v1/auth/me").json()
        with SessionLocal() as db:
            counts = [
                db.scalar(select(func.count()).select_from(model))
                for model in [User, RoleProfile, Audit]
            ]
        new_name = "new-contract-" + secrets.token_hex(8)
        function = "profile_output" if operation.startswith("role-") else "account_output"
        original = getattr(routes, function)
        with monkeypatch.context() as patch:
            patch.setattr(routes, function, lambda *args: {**original(*args), "version": 0})
            with pytest.raises(ValidationError):
                if operation == "role-create":
                    reg.post(
                        "/api/v1/roles",
                        headers=headers,
                        json={
                            "name": new_name,
                            "base_role": "operator",
                            "permissions": ["stations.read"],
                        },
                    )
                elif operation == "role-edit":
                    reg.patch(
                        f"/api/v1/roles/{pid}",
                        headers=headers,
                        json={
                            "version": 1,
                            "name": new_name,
                            "base_role": "operator",
                            "permissions": ["stations.read", "applications.read"],
                        },
                    )
                elif operation == "account-create":
                    with SessionLocal() as db:
                        oid = db.get(User, uid).operator_id
                    reg.post(
                        "/api/v1/accounts",
                        headers=headers,
                        json={
                            "username": new_name,
                            "password": password,
                            "role_profile_id": pid,
                            "operator_id": oid,
                        },
                    )
                else:
                    reg.patch(
                        f"/api/v1/accounts/{uid}",
                        headers=headers,
                        json={
                            "version": 1,
                            "role_profile_id": pid,
                            "enabled": False,
                            "allowed_districts": [],
                            "reason": "rollback proof",
                        },
                    )
        assert owner.get("/api/v1/auth/me").json() == before
        with SessionLocal() as db:
            assert [
                db.scalar(select(func.count()).select_from(model))
                for model in [User, RoleProfile, Audit]
            ] == counts
            profile = db.get(RoleProfile, pid)
            target = db.get(User, uid)
            assert profile.version == 1 and profile.permissions == ["stations.read"]
            assert target.version == 1 and target.enabled and target.allowed_districts is None
        listing = reg.get("/api/v1/accounts", params={"q": names[0]}).json()
        assert listing["total"] == 1
        assert set(listing["items"][0]) == {
            "id",
            "username",
            "role",
            "operator_id",
            "enabled",
            "must_change_password",
            "role_profile_id",
            "version",
            "allowed_districts",
            "permissions",
        }
        assert owner.get("/api/v1/accounts/options").status_code == 403
    finally:
        reg.close()
        owner.close()
        with SessionLocal.begin() as db:
            db.get(User, uid).role_profile_id = None
            db.flush()
            db.execute(delete(RoleProfile).where(RoleProfile.id == pid))


def test_management_list_total_and_grants_use_one_snapshot(accounts, staff):
    from sqlalchemy import event

    from app.db import engine

    names, password, _ = accounts
    reg, _ = login(staff[0][0], password)
    with SessionLocal() as db:
        uid = db.scalar(select(User.id).where(User.username == names[0]))
    changed = False

    def concurrent_rename(connection, cursor, statement, parameters, context, executemany):
        nonlocal changed
        if not changed and "FROM users" in statement and "ORDER BY users.id" in statement:
            changed = True
            with SessionLocal.begin() as other:
                other.get(User, uid).username = "concurrent-" + secrets.token_hex(8)

    event.listen(engine, "after_cursor_execute", concurrent_rename)
    try:
        response = reg.get("/api/v1/accounts", params={"q": names[0]})
    finally:
        event.remove(engine, "after_cursor_execute", concurrent_rename)
        reg.close()
    assert changed and response.status_code == 200, response.text
    assert response.json()["total"] == 1
    assert [row["username"] for row in response.json()["items"]] == [names[0]]
