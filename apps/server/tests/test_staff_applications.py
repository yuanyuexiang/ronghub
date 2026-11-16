import secrets
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, func, select

from app.db import SessionLocal
from app.main import app
from app.models import (
    Audit,
    LoginSession,
    RoleProfile,
    StaffApplication,
    StaffApplicationAction,
    User,
    VerifiedPhone,
)


def client():
    return TestClient(app, client=("staff-request-" + secrets.token_hex(8), 1))


def login(c, name, password):
    result = c.post("/api/v1/auth/login", json={"username": name, "password": password})
    assert result.status_code == 200, result.text
    return {"x-csrf-token": result.json()["csrf_token"]}


def submit(c, role="association", phone=None, **extra):
    captcha = c.get("/api/v1/auth/captcha").json()
    return c.post(
        "/api/v1/auth/staff-enrollment",
        json={
            "requested_role": role,
            "applicant_name": "申请人-" + secrets.token_hex(4),
            "contact_phone": phone or "139" + f"{secrets.randbelow(100000000):08d}",
            "captcha_id": captcha["captcha_id"],
            "captcha_answer": "ABCDE",
            **extra,
        },
    )


def cleanup(ids, profiles, extra_users=()):
    with SessionLocal.begin() as db:
        users = list(
            db.scalars(
                select(StaffApplication.user_id).where(
                    StaffApplication.id.in_(ids), StaffApplication.user_id.is_not(None)
                )
            )
        )
        users.extend(extra_users)
        db.execute(delete(StaffApplication).where(StaffApplication.id.in_(ids)))
        db.execute(delete(Audit).where(Audit.user_id.in_(users)))
        db.execute(delete(LoginSession).where(LoginSession.user_id.in_(users)))
        db.execute(delete(User).where(User.id.in_(users)))
        db.execute(delete(RoleProfile).where(RoleProfile.id.in_(profiles)))


def test_request_has_no_grants_private_cookie_and_explicit_human_approval(accounts, staff):
    applicant, outsider, reg, association = client(), client(), client(), client()
    ids = []
    profiles = []
    try:
        headers = login(reg, staff[0][0], accounts[1])
        login(association, staff[0][1], accounts[1])
        assert submit(applicant, status="approved").status_code == 422
        assert applicant.get("/api/v1/auth/staff-enrollment/me").status_code == 401
        with SessionLocal() as db:
            before = db.scalar(select(func.count()).select_from(User))
        created = submit(applicant)
        assert created.status_code == 201, created.text
        aid = created.json()["id"]
        ids.append(aid)
        assert "contact_phone" not in created.json() and created.json()["username"] is None
        assert "HttpOnly" in created.headers["set-cookie"]
        assert outsider.get("/api/v1/auth/staff-enrollment/me").status_code == 401
        assert applicant.get("/api/v1/staff-applications").status_code == 401
        assert association.get("/api/v1/staff-applications").status_code == 403
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(User)) == before
        profile = reg.post(
            "/api/v1/roles",
            headers=headers,
            json={
                "name": "staff-approval-" + secrets.token_hex(8),
                "base_role": "association",
                "permissions": ["stations.read", "content.read"],
            },
        )
        assert profile.status_code == 201
        pid = profile.json()["id"]
        profiles.append(pid)
        assert any(
            row["id"] == pid
            for row in reg.get(
                "/api/v1/staff-applications/role-options?base_role=association"
            ).json()["items"]
        )
        account = {
            "username": "approved-" + secrets.token_hex(8),
            "temporary_password": "Temporary-staff-" + secrets.token_hex(8),
            "role_profile_id": pid,
            "role_profile_version": 1,
            "allowed_districts": ["测试"],
        }
        url = f"/api/v1/staff-applications/{aid}/review"
        assert (
            reg.post(
                url,
                headers=headers,
                json={"action": "approve", "version": 1, "comment": "审核通过"},
            ).status_code
            == 422
        )
        approved = reg.post(
            url,
            headers=headers,
            json={
                "action": "approve",
                "version": 1,
                "comment": "已线下核验，初始凭据另行交付",
                "account": account,
            },
        )
        assert approved.status_code == 200, approved.text
        data = approved.json()
        assert data["status"] == "approved" and data["version"] == 2
        assert data["grant_snapshot"]["allowed_districts"] == ["测试"]
        assert account["temporary_password"] not in approved.text
        state = applicant.get("/api/v1/auth/staff-enrollment/me").json()
        assert (
            state["username"] == account["username"] and state["notification_status"] == "not_sent"
        )
        assert "grant_snapshot" not in state and "user_id" not in state
        assert (
            reg.post(
                url,
                headers=headers,
                json={
                    "action": "approve",
                    "version": 1,
                    "comment": "duplicate",
                    "account": account,
                },
            ).status_code
            == 409
        )
        new = client()
        try:
            nh = login(new, account["username"], account["temporary_password"])
            identity = new.get("/api/v1/auth/me").json()
            assert identity["must_change_password"] is True and identity["role"] == "association"
            assert identity["allowed_districts"] == ["测试"]
            assert new.get("/api/v1/stations").status_code == 403
            change = new.post(
                "/api/v1/auth/password",
                headers=nh,
                json={
                    "current_password": account["temporary_password"],
                    "new_password": "Staff-final-" + secrets.token_hex(8),
                },
            )
            assert change.status_code == 200, change.text
            with SessionLocal() as db:
                assert db.get(VerifiedPhone, data["user_id"]) is None
                assert db.get(User, data["user_id"]).must_change_password is False
                assert (
                    db.scalar(
                        select(func.count())
                        .select_from(StaffApplicationAction)
                        .where(StaffApplicationAction.application_id == aid)
                    )
                    == 1
                )
            assert applicant.get("/api/v1/auth/staff-enrollment/me").status_code == 401
        finally:
            new.close()
    finally:
        for c in [applicant, outsider, reg, association]:
            c.close()
        cleanup(ids, profiles)


def test_reject_reapply_role_mismatch_duplicate_username_and_concurrent_review(accounts, staff):
    applicant, reg = client(), client()
    ids = []
    profiles = []
    try:
        headers = login(reg, staff[0][0], accounts[1])
        phone = "139" + f"{secrets.randbelow(100000000):08d}"
        first = submit(applicant, phone=phone)
        assert first.status_code == 201
        aid = first.json()["id"]
        ids.append(aid)
        assert submit(client(), phone=phone).status_code == 409
        result = reg.post(
            f"/api/v1/staff-applications/{aid}/review",
            headers=headers,
            json={"action": "reject", "version": 1, "comment": "请补充身份核验后重新申请"},
        )
        assert result.status_code == 200
        assert applicant.get("/api/v1/auth/staff-enrollment/me").json()["status"] == "rejected"
        again = submit(applicant, phone=phone)
        assert again.status_code == 201, again.text
        aid = again.json()["id"]
        ids.append(aid)
        for base in ["association", "regulator"]:
            p = reg.post(
                "/api/v1/roles",
                headers=headers,
                json={
                    "name": "staff-review-" + secrets.token_hex(8),
                    "base_role": base,
                    "permissions": ["stations.read", "content.read"],
                },
            )
            assert p.status_code == 201
            profiles.append(p.json()["id"])
        account = {
            "username": accounts[0][0],
            "temporary_password": "Tmp-staff-safe-123456",
            "role_profile_id": profiles[1],
            "role_profile_version": 1,
            "allowed_districts": None,
        }
        url = f"/api/v1/staff-applications/{aid}/review"
        assert (
            reg.post(
                url,
                headers=headers,
                json={"action": "approve", "version": 1, "comment": "错身份", "account": account},
            ).status_code
            == 422
        )
        account["role_profile_id"] = profiles[0]
        assert (
            reg.post(
                url,
                headers=headers,
                json={"action": "approve", "version": 1, "comment": "重名", "account": account},
            ).status_code
            == 409
        )
        assert reg.get(f"/api/v1/staff-applications/{aid}").json()["status"] == "pending"
        account["username"] = "concurrent-staff-" + secrets.token_hex(8)
        peers = [client(), client()]
        try:
            hs = [login(c, staff[0][0], accounts[1]) for c in peers]
            gate = Barrier(2)

            def review(args):
                c, h = args
                gate.wait(timeout=5)
                return c.post(
                    url,
                    headers=h,
                    json={
                        "action": "approve",
                        "version": 1,
                        "comment": "已核验",
                        "account": account,
                    },
                ).status_code

            with ThreadPoolExecutor(max_workers=2) as pool:
                assert sorted(pool.map(review, zip(peers, hs, strict=True))) == [200, 409]
            with SessionLocal() as db:
                assert (
                    db.scalar(
                        select(func.count())
                        .select_from(User)
                        .where(User.username == account["username"])
                    )
                    == 1
                )
                assert (
                    db.scalar(
                        select(func.count())
                        .select_from(StaffApplicationAction)
                        .where(StaffApplicationAction.application_id == aid)
                    )
                    == 1
                )
        finally:
            for c in peers:
                c.close()
    finally:
        applicant.close()
        reg.close()
        cleanup(ids, profiles)


def test_staff_review_global_scope_grant_ceiling_profile_version_and_frozen_history(
    accounts, staff, business_login
):
    applicant, root, low, regional = client(), client(), client(), client()
    ids = []
    profiles = []
    users = []
    try:
        headers = login(root, staff[0][0], accounts[1])
        for role, grants in [
            ("regulator", ["stations.read", "accounts.manage"]),
            ("association", ["stations.read", "content.read"]),
            ("association", ["stations.read"]),
        ]:
            response = root.post(
                "/api/v1/roles",
                headers=headers,
                json={
                    "name": "staff-scope-" + secrets.token_hex(8),
                    "base_role": role,
                    "permissions": grants,
                },
            )
            assert response.status_code == 201, response.text
            profiles.append(response.json()["id"])
        for c, scope in [(low, None), (regional, [])]:
            username = "staff-admin-" + secrets.token_hex(8)
            response = root.post(
                "/api/v1/accounts",
                headers=headers,
                json={
                    "username": username,
                    "password": accounts[1],
                    "role_profile_id": profiles[0],
                    "allowed_districts": scope,
                },
            )
            assert response.status_code == 201
            users.append(response.json()["id"])
            business_login(c, username, accounts[1])
        created = submit(applicant)
        assert created.status_code == 201
        aid = created.json()["id"]
        ids.append(aid)
        for path in (
            "/api/v1/staff-applications",
            f"/api/v1/staff-applications/{aid}",
            "/api/v1/staff-applications/role-options?base_role=association",
        ):
            assert regional.get(path).status_code == 403
        opts = low.get("/api/v1/staff-applications/role-options?base_role=association").json()[
            "items"
        ]
        assert profiles[1] not in {p["id"] for p in opts} and profiles[2] in {p["id"] for p in opts}
        lh = {"x-csrf-token": low.get("/api/v1/auth/me").json()["csrf_token"]}
        account = {
            "username": "staff-snapshot-" + secrets.token_hex(8),
            "temporary_password": "Staff-snapshot-secret-123",
            "role_profile_id": profiles[1],
            "role_profile_version": 1,
            "allowed_districts": None,
        }
        url = f"/api/v1/staff-applications/{aid}/review"
        assert (
            low.post(
                url,
                headers=lh,
                json={
                    "action": "approve",
                    "version": 1,
                    "comment": "越权应拒绝",
                    "account": account,
                },
            ).status_code
            == 403
        )
        account["role_profile_id"] = profiles[2]
        with SessionLocal.begin() as db:
            db.get(RoleProfile, profiles[2]).version = 2
        assert (
            low.post(
                url,
                headers=lh,
                json={
                    "action": "approve",
                    "version": 1,
                    "comment": "旧角色应拒绝",
                    "account": account,
                },
            ).status_code
            == 409
        )
        account["role_profile_version"] = 2
        accepted = low.post(
            url,
            headers=lh,
            json={"action": "approve", "version": 1, "comment": "明确授权", "account": account},
        )
        assert accepted.status_code == 200, accepted.text
        with SessionLocal.begin() as db:
            row = db.get(RoleProfile, profiles[2])
            row.version = 3
            row.permissions = ["stations.read", "content.read"]
        historical = root.get(f"/api/v1/staff-applications/{aid}").json()
        assert historical["grant_snapshot"]["profile_version"] == 2 and historical[
            "grant_snapshot"
        ]["permissions"] == ["stations.read"]
        assert (
            historical["history"][0]["snapshot"]["grant_snapshot"] == historical["grant_snapshot"]
        )
        assert account["temporary_password"] not in str(historical)
    finally:
        for c in [applicant, root, low, regional]:
            c.close()
        cleanup(ids, profiles, users)


def test_staff_queues_and_grantable_roles_page_beyond_twenty(accounts, staff):
    root = client()
    ids = []
    profiles = []
    prefix = "staff-pages-" + secrets.token_hex(8)
    try:
        login(root, staff[0][0], accounts[1])
        base = secrets.randbelow(99999970)
        with SessionLocal.begin() as db:
            for index in range(25):
                row = StaffApplication(
                    requested_role="association",
                    applicant_name=prefix + str(index),
                    contact_phone="138" + f"{base + index:08d}",
                )
                profile = RoleProfile(
                    name=prefix + str(index), base_role="association", permissions=["stations.read"]
                )
                db.add_all([row, profile])
                db.flush()
                ids.append(row.id)
                profiles.append(profile.id)
        for path, extra in [
            ("/api/v1/staff-applications", {}),
            ("/api/v1/staff-applications/role-options", {"base_role": "association"}),
        ]:
            first = root.get(path, params={"q": prefix, **extra}).json()
            second = root.get(path, params={"q": prefix, "page": 2, **extra}).json()
            assert first["total"] == second["total"] == 25
            assert len(first["items"]) == 20 and len(second["items"]) == 5
            assert not ({r["id"] for r in first["items"]} & {r["id"] for r in second["items"]})
            assert root.get(path, params={"q": prefix + "%", **extra}).json()["total"] == 0
    finally:
        root.close()
        cleanup(ids, profiles)


def test_invalid_projection_rolls_back_account_and_approval(accounts, staff, monkeypatch):
    from app.routers import staff_applications as routes

    applicant, root = client(), client()
    ids = []
    profiles = []
    try:
        headers = login(root, staff[0][0], accounts[1])
        created = submit(applicant)
        assert created.status_code == 201
        aid = created.json()["id"]
        ids.append(aid)
        p = root.post(
            "/api/v1/roles",
            headers=headers,
            json={
                "name": "staff-rollback-" + secrets.token_hex(8),
                "base_role": "association",
                "permissions": ["stations.read"],
            },
        )
        assert p.status_code == 201
        profiles.append(p.json()["id"])
        username = "staff-rollback-" + secrets.token_hex(8)
        monkeypatch.setattr(routes, "detail_output", lambda db, row: {"invalid": True})
        with pytest.raises(ValidationError):
            root.post(
                f"/api/v1/staff-applications/{aid}/review",
                headers=headers,
                json={
                    "action": "approve",
                    "version": 1,
                    "comment": "确认身份",
                    "account": {
                        "username": username,
                        "temporary_password": "Rollback-private-123456",
                        "role_profile_id": profiles[0],
                        "role_profile_version": 1,
                        "allowed_districts": None,
                    },
                },
            )
        with SessionLocal() as db:
            assert db.get(StaffApplication, aid).status == "pending"
            assert db.scalar(select(User.id).where(User.username == username)) is None
            assert (
                db.scalar(
                    select(StaffApplicationAction.id).where(
                        StaffApplicationAction.application_id == aid
                    )
                )
                is None
            )
        assert "staff_applications_pending" in root.get("/api/v1/dashboard").json()
    finally:
        applicant.close()
        root.close()
        cleanup(ids, profiles)


def test_staff_submission_rate_expired_progress_and_production_boundary(accounts, monkeypatch):
    from datetime import timedelta

    from app.config import settings
    from app.models import StaffApplicantSession, now

    applicant = client()
    ids = []
    try:
        for _ in range(5):
            result = submit(applicant)
            assert result.status_code == 201, result.text
            ids.append(result.json()["id"])
        limited = submit(applicant)
        assert (
            limited.status_code == 429
            and "申请提交" in limited.text
            and limited.headers["retry-after"] == "3600"
        )
        with SessionLocal.begin() as db:
            row = db.scalar(
                select(StaffApplicantSession).where(StaffApplicantSession.application_id == ids[-1])
            )
            row.expires_at = now() - timedelta(seconds=1)
        assert applicant.get("/api/v1/auth/staff-enrollment/me").status_code == 401
        monkeypatch.setattr(settings, "demo_mode", False)
        assert applicant.get("/api/v1/auth/staff-enrollment/options").json()["enabled"] is False
        assert submit(applicant).status_code == 409
    finally:
        applicant.close()
        cleanup(ids, [])
