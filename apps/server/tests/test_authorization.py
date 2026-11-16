import secrets

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.main import app
from app.models import Audit, LoginSession, RoleProfile, User


def test_capability_revocation_and_bounded_delegation(accounts, staff, business_login):
    names, password, stations = accounts
    clients = [TestClient(app, client=("test-" + secrets.token_hex(6), 1)) for _ in range(3)]
    role_ids = []
    new_users = []
    actor_ids = []
    try:
        reg, op, limited = clients

        def login(c, name):
            r = business_login(c, name, password)
            assert r.status_code == 200
            return {"x-csrf-token": r.json()["csrf_token"]}

        rh = login(reg, staff[0][0])
        oh = login(op, names[0])
        with SessionLocal() as db:
            target = db.scalar(select(User).where(User.username == names[0]))
            uid = target.id
            actor_ids = [uid, db.scalar(select(User.id).where(User.username == staff[0][0]))]

        def role(name, base, caps):
            r = reg.post(
                "/api/v1/roles",
                headers=rh,
                json={
                    "name": name + "-" + secrets.token_hex(6),
                    "base_role": base,
                    "permissions": caps,
                },
            )
            assert r.status_code == 201
            role_ids.append(r.json()["id"])
            return r.json()

        readonly = role("只读企业", "operator", ["stations.read", "applications.read"])
        assert (
            reg.patch(
                f"/api/v1/accounts/{uid}",
                headers=rh,
                json={
                    "version": 1,
                    "role_profile_id": readonly["id"],
                    "enabled": True,
                    "reason": "演示只读分工",
                },
            ).status_code
            == 200
        )
        assert op.get("/api/v1/auth/me").status_code == 401
        oh = login(op, names[0])
        assert op.get("/api/v1/applications").status_code == 200
        assert (
            op.post(
                "/api/v1/applications",
                headers=oh,
                json={"station_id": stations[0], "kind": "access"},
            ).status_code
            == 403
        )
        assert op.get("/api/v1/subsidy/claims").status_code == 403
        dashboard = op.get("/api/v1/dashboard").json()
        assert "subsidy_total" not in dashboard and "subsidies_pending" not in dashboard
        assert "energy_total" not in dashboard
        withdrawn = reg.patch(
            "/api/v1/roles/" + str(readonly["id"]),
            headers=rh,
            json={**readonly, "version": 1, "permissions": ["stations.read"]},
        )
        assert withdrawn.status_code == 200
        assert op.get("/api/v1/auth/me").status_code == 401
        login(op, names[0])
        assert op.get("/api/v1/applications").status_code == 403
        assert op.get("/api/v1/dashboard").json()["pending"] is None
        assert "applications_pending" not in op.get("/api/v1/dashboard").json()
        # Per-account disabling also revokes sessions permanently until a fresh login.
        assert (
            reg.patch(
                f"/api/v1/accounts/{uid}",
                headers=rh,
                json={
                    "version": 2,
                    "role_profile_id": readonly["id"],
                    "enabled": False,
                    "reason": "停用测试",
                },
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
            reg.patch(
                f"/api/v1/accounts/{uid}",
                headers=rh,
                json={
                    "version": 3,
                    "role_profile_id": readonly["id"],
                    "enabled": True,
                    "reason": "恢复测试",
                },
            ).status_code
            == 200
        )
        assert op.get("/api/v1/auth/me").status_code == 401
        login(op, names[0])
        manager = role(
            "受限管理员", "regulator", ["stations.read", "roles.manage", "accounts.manage"]
        )
        username = "limited-" + secrets.token_hex(6)
        r = reg.post(
            "/api/v1/accounts",
            headers=rh,
            json={"username": username, "password": password, "role_profile_id": manager["id"]},
        )
        assert r.status_code == 201
        new_users.append(r.json()["id"])
        actor_ids.extend(new_users)
        lh = login(limited, username)
        denied = limited.post(
            "/api/v1/roles",
            headers=lh,
            json={
                "name": "越权-" + secrets.token_hex(6),
                "base_role": "operator",
                "permissions": ["stations.read", "subsidy.read"],
            },
        )
        assert denied.status_code == 403
        assert (
            limited.patch(
                "/api/v1/roles/" + str(manager["id"]), headers=lh, json={**manager, "version": 1}
            ).status_code
            == 403
        )
        assert (
            limited.patch(
                "/api/v1/accounts/" + str(new_users[0]),
                headers=lh,
                json={
                    "version": 1,
                    "role_profile_id": manager["id"],
                    "enabled": True,
                    "reason": "自行变更",
                },
            ).status_code
            == 403
        )
        # A genuine subset remains delegable.
        r = limited.post(
            "/api/v1/roles",
            headers=lh,
            json={
                "name": "子角色-" + secrets.token_hex(6),
                "base_role": "operator",
                "permissions": ["stations.read"],
            },
        )
        assert r.status_code == 201
        role_ids.append(r.json()["id"])
        assert (
            limited.get("/api/v1/statistics?start_date=2026-11-01&end_date=2026-11-02").status_code
            == 403
        )
    finally:
        for c in clients:
            c.close()
        with SessionLocal.begin() as db:
            db.execute(
                delete(Audit).where(
                    Audit.user_id.in_(actor_ids),
                    Audit.action.like("role.%")
                    | Audit.action.like("account.%")
                    | (Audit.action == "password.change"),
                )
            )
            db.execute(delete(LoginSession).where(LoginSession.user_id.in_(new_users)))
            for user in db.scalars(select(User).where(User.role_profile_id.in_(role_ids))):
                user.role_profile_id = None
            db.flush()
            db.execute(delete(User).where(User.id.in_(new_users)))
            db.execute(delete(RoleProfile).where(RoleProfile.id.in_(role_ids)))


def test_every_private_api_route_has_identity_and_capability_mapping():
    from fastapi.routing import APIRoute
    from starlette.requests import Request

    from app.authorization import request_capability
    from app.dependencies import current_user

    public = {
        ("/api/v1/auth/login", "POST"),
        ("/api/v1/health", "GET"),
        ("/api/v1/enrollment", "POST"),
        ("/api/v1/enrollment/login", "POST"),
    }
    from app.enrollment import applicant

    def has_identity(dependant, identity=current_user):
        return dependant.call is identity or any(
            has_identity(d, identity) for d in dependant.dependencies
        )

    for route in app.routes:
        if (
            not isinstance(route, APIRoute)
            or not route.path.startswith("/api/v1/")
            or all((route.path, method) in public for method in route.methods)
        ):
            continue
        if route.path.startswith("/api/v1/enrollment/"):
            assert has_identity(route.dependant, applicant), route.path
            continue
        assert has_identity(route.dependant), route.path
        for method in route.methods:
            request = Request(
                {
                    "type": "http",
                    "path": route.path,
                    "method": method,
                    "query_string": b"",
                    "headers": [],
                }
            )
            cap = request_capability(request)
            assert cap or route.path in {"/api/v1/auth/me", "/api/v1/auth/logout"}, route.path
