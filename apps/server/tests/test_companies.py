import secrets

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db import SessionLocal
from app.main import app
from app.models import Audit, Station


def test_profile_contacts_credit_search_isolation_and_company_disable(accounts, staff):
    names, password, stations = accounts
    clients = [TestClient(app, client=("test-" + secrets.token_hex(6), 1)) for _ in range(4)]
    with SessionLocal() as db:
        oids = [db.get(Station, s).operator_id for s in stations]
    try:
        headers = []
        for c, name in zip(clients, [names[0], names[1], staff[0][0], staff[0][1]], strict=True):
            r = c.post("/api/v1/auth/login", json={"username": name, "password": password})
            assert r.status_code == 200
            headers.append({"x-csrf-token": r.json()["csrf_token"]})
        op, other, reg, assoc = clients
        url = f"/api/v1/companies/{oids[0]}"
        original = op.get(url).json()
        body = {
            "version": original["version"],
            "name": original["name"],
            "credit_code": "A" + secrets.token_hex(9).upper()[:17],
            "registration_address": "测试地址",
            "legal_representative": "测试负责人",
            "service_phone": "123456",
            "contacts": [
                {"name": "张先生", "phone": "100001", "primary": True},
                {"name": "李女士", "phone": "100002"},
            ],
        }
        saved = op.patch(url, headers=headers[0], json=body)
        assert saved.status_code == 200
        assert len(saved.json()["contacts"]) == 2
        assert other.get(url).status_code == 404
        assert other.patch(url, headers=headers[1], json=body).status_code == 404
        assert assoc.get(url).status_code == 403
        assert op.patch(url, headers=headers[0], json=body).status_code == 409
        assert (
            op.patch(
                url, headers=headers[0], json={**body, "version": 2, "credit_code": "bad"}
            ).status_code
            == 422
        )
        results = reg.get(
            "/api/v1/companies", params={"credit_code": body["credit_code"].lower()}
        ).json()
        assert [r["id"] for r in results["items"]] == [oids[0]]
        assert "contacts" not in results["items"][0]
        assert len(reg.get(url).json()["history"]) == 1
        assert (
            op.post(
                url + "/status",
                headers=headers[0],
                json={"version": 2, "enabled": False, "reason": "越权"},
            ).status_code
            == 403
        )
        assert (
            reg.post(
                url + "/status",
                headers=headers[2],
                json={"version": 2, "enabled": False, "reason": "演示停用"},
            ).status_code
            == 200
        )
        assert op.get("/api/v1/auth/me").status_code == 401
        assert (
            op.post(
                "/api/v1/auth/login", json={"username": names[0], "password": password}
            ).status_code
            == 401
        )
        assert (
            reg.post(
                url + "/status",
                headers=headers[2],
                json={"version": 3, "enabled": True, "reason": "演示恢复"},
            ).status_code
            == 200
        )
        assert op.get("/api/v1/auth/me").status_code == 401
        assert (
            op.post(
                "/api/v1/auth/login", json={"username": names[0], "password": password}
            ).status_code
            == 200
        )
    finally:
        for c in clients:
            c.close()
        with SessionLocal.begin() as db:
            db.execute(
                delete(Audit).where(
                    Audit.object_id.in_(oids),
                    Audit.action.in_(["company.update", "company.status"]),
                )
            )
