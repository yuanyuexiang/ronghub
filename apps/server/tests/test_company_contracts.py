import secrets

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, event, func, select

from app.db import SessionLocal, engine
from app.main import app
from app.models import Audit, Operator, Station, User
from app.routers import companies


def login(name, password):
    client = TestClient(app, client=("company-proof-" + secrets.token_hex(6), 1))
    result = client.post("/api/v1/auth/login", json={"username": name, "password": password})
    assert result.status_code == 200, result.text
    return client, {"x-csrf-token": result.json()["csrf_token"]}


@pytest.mark.parametrize("status", [False, True])
def test_invalid_projection_rolls_back_profile_status_sessions_and_audit(
    accounts, staff, monkeypatch, status
):
    names, password, stations = accounts
    operator, _ = login(names[0], password)
    regulator, headers = login(staff[0][0], password)
    with SessionLocal() as db:
        oid = db.get(Station, stations[0]).operator_id
        audits = db.scalar(select(func.count()).select_from(Audit))
    path = f"/api/v1/companies/{oid}"
    before = regulator.get(path).json()
    original = companies.output
    with monkeypatch.context() as patch:
        patch.setattr(
            companies,
            "output",
            lambda record, private=False: {**original(record, private), "enabled": "not-a-boolean"},
        )
        with pytest.raises(ValidationError):
            if status:
                regulator.post(
                    path + "/status",
                    headers=headers,
                    json={
                        "version": before["version"],
                        "enabled": False,
                        "reason": "rollback proof",
                    },
                )
            else:
                regulator.patch(
                    path,
                    headers=headers,
                    json={"version": before["version"], "name": before["name"] + "-changed"},
                )
    assert regulator.get(path).json() == before
    assert operator.get("/api/v1/auth/me").status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Audit)) == audits
    operator.close()
    regulator.close()


def test_csv_scope_bound_and_no_success_audit_on_overflow(accounts, staff, monkeypatch):
    names, password, stations = accounts
    reg, _ = login(staff[0][0], password)
    op, _ = login(names[0], password)
    assoc, _ = login(staff[0][1], password)
    with SessionLocal.begin() as db:
        oid = db.get(Station, stations[0]).operator_id
        db.get(Operator, oid).name = "=FORMULA_%_" + secrets.token_hex(6)
        name = db.get(Operator, oid).name
        db.get(Operator, oid).contacts = [{"name": "secret", "phone": "private-phone"}]
    try:
        assert assoc.get("/api/v1/companies/export").status_code == 403
        result = op.get("/api/v1/companies/export")
        assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
        assert "'" + name in result.text and "private-phone" not in result.text
        assert len(result.text.splitlines()) == 2
        listing = reg.get("/api/v1/companies", params={"q": "%_"}).json()
        assert [item["id"] for item in listing["items"]] == [oid]
        assert "contacts" not in listing["items"][0]
        with SessionLocal() as db:
            before = db.scalar(
                select(func.count()).select_from(Audit).where(Audit.action == "company.export")
            )
        monkeypatch.setattr(companies, "EXPORT_LIMIT", 1)
        result = reg.get("/api/v1/companies/export")
        assert result.status_code == 422 and "未截断" in result.text
        with SessionLocal() as db:
            assert (
                db.scalar(
                    select(func.count()).select_from(Audit).where(Audit.action == "company.export")
                )
                == before
            )
        assert reg.get("/api/v1/companies/export", params={"q": name}).status_code == 200
    finally:
        reg.close()
        op.close()
        assoc.close()
        with SessionLocal.begin() as db:
            db.execute(
                delete(Audit).where(
                    Audit.action == "company.export",
                    Audit.user_id.in_(
                        select(User.id).where(User.username.in_([names[0], staff[0][0]]))
                    ),
                )
            )


def test_company_list_count_uses_same_snapshot_as_rows(accounts, staff):
    _, password, stations = accounts
    reg, _ = login(staff[0][0], password)
    with SessionLocal() as db:
        oid = db.get(Station, stations[0]).operator_id
        name = db.get(Operator, oid).name
    changed = False

    def rename(connection, cursor, statement, parameters, context, executemany):
        nonlocal changed
        if not changed and "FROM operators" in statement and "ORDER BY operators.id" in statement:
            changed = True
            with SessionLocal.begin() as other:
                other.get(Operator, oid).name = "renamed-" + secrets.token_hex(6)

    event.listen(engine, "after_cursor_execute", rename)
    try:
        result = reg.get("/api/v1/companies", params={"q": name})
    finally:
        event.remove(engine, "after_cursor_execute", rename)
        reg.close()
    assert changed and result.status_code == 200, result.text
    assert result.json()["total"] == 1
    assert [row["name"] for row in result.json()["items"]] == [name]
