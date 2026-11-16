import secrets
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.main import app
from app.models import Audit, RoleProfile, User


def test_audit_filters_redaction_paging_and_permissions(accounts, staff):
    names, password, _ = accounts
    created = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
    profile_id = None
    with SessionLocal.begin() as db:
        uid = db.scalar(select(User.id).where(User.username == names[0]))
        reg_id = db.scalar(select(User.id).where(User.username == staff[0][0]))
        for i in range(25):
            db.add(
                Audit(
                    user_id=uid,
                    action="password.reset",
                    object_id=uid,
                    created_at=created,
                    detail={
                        "version": i,
                        "scores": {
                            "valid": "95",
                            "missing": None,
                            "secret": "secret-proof",
                            "invalid": "NaN",
                            "boolean": True,
                        },
                        "password": "secret-proof",
                        "token": "secret-proof",
                        "target_username": names[0],
                        "before": {"enabled": True, "password_hash": "secret-proof"},
                        "arbitrary": {"secret": "secret-proof"},
                    },
                )
            )
        db.add(
            Audit(
                user_id=uid,
                action="sync.enqueue",
                object_id=uid,
                created_at=created + timedelta(days=1),
                detail={"source": "demo"},
            )
        )
    clients = [TestClient(app, client=("audit-" + secrets.token_hex(8), 1)) for _ in range(3)]
    try:
        for client, username in zip(clients, [staff[0][0], names[0], staff[0][1]]):
            assert (
                client.post(
                    "/api/v1/auth/login", json={"username": username, "password": password}
                ).status_code
                == 200
            )
        reg, operator, association = clients
        assert TestClient(app).get("/api/v1/audits").status_code == 401
        assert operator.get("/api/v1/audits").status_code == 403
        assert association.get("/api/v1/audits").status_code == 403
        params = {
            "actor": names[0],
            "action": "password.reset",
            "object_id": uid,
            "start_at": created.isoformat(),
            "end_at": (created + timedelta(days=1)).isoformat(),
        }
        result = reg.get("/api/v1/audits", params=params)
        assert result.status_code == 200
        body = result.json()
        assert body["total"] == 25 and len(body["items"]) == 20
        assert "secret-proof" not in result.text and "password_hash" not in result.text
        assert body["items"][0]["detail"]["before"] == {"enabled": True}
        assert body["items"][0]["detail"]["scores"] == {"valid": "95", "missing": None}
        assert body["items"][0]["result"] == "committed"
        second = reg.get("/api/v1/audits", params={**params, "page": 2}).json()
        assert len(second["items"]) == 5
        ids = [row["id"] for row in body["items"] + second["items"]]
        assert ids == sorted(ids, reverse=True) and len(set(ids)) == 25
        assert (
            reg.get("/api/v1/audits", params={**params, "actor": names[0] + "%"}).json()["total"]
            == 0
        )
        assert (
            reg.get("/api/v1/audits", params={**params, "object_id": uid + 999999}).json()["total"]
            == 0
        )
        assert (
            reg.get("/api/v1/audits", params={"start_at": "2026-10-05T00:00:00"}).status_code == 422
        )
        assert (
            reg.get(
                "/api/v1/audits",
                params={"start_at": created.isoformat(), "end_at": created.isoformat()},
            ).status_code
            == 422
        )
        for invalid in [{"page": 0}, {"page_size": 101}]:
            assert reg.get("/api/v1/audits", params=invalid).status_code == 422
        with SessionLocal.begin() as db:
            user = db.get(User, reg_id)
            user.allowed_districts = ["测试"]
        assert reg.get("/api/v1/audits").status_code == 403
        with SessionLocal.begin() as db:
            user = db.get(User, reg_id)
            user.allowed_districts = None
            profile = RoleProfile(
                name="audit-denied-" + secrets.token_hex(8),
                base_role="regulator",
                permissions=["stations.read"],
            )
            db.add(profile)
            db.flush()
            profile_id = profile.id
            user.role_profile_id = profile.id
        assert reg.get("/api/v1/audits").status_code == 403
    finally:
        for client in clients:
            client.close()
        with SessionLocal.begin() as db:
            user = db.get(User, reg_id)
            user.role_profile_id = None
            user.allowed_districts = None
            if profile_id:
                db.execute(delete(RoleProfile).where(RoleProfile.id == profile_id))
            db.execute(delete(Audit).where(Audit.user_id == uid))
