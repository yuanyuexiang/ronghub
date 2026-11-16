import secrets

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import (
    Audit,
    EnrollmentSession,
    LoginSession,
    Operator,
    Registration,
    RegistrationAction,
    RegistrationAttachment,
    RoleProfile,
    User,
)


def test_registration_materials_rejection_approval_and_login(staff, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    clients = [TestClient(app, client=("test-enroll-" + secrets.token_hex(6), 1)) for _ in range(3)]
    ids = []
    profile_id = None
    created_user = None
    created_company = None
    try:
        applicant, reg, other = clients
        password = staff[1]
        auth = reg.post("/api/v1/auth/login", json={"username": staff[0][0], "password": password})
        assert auth.status_code == 200
        headers = {"x-csrf-token": auth.json()["csrf_token"]}
        role = reg.post(
            "/api/v1/roles",
            headers=headers,
            json={
                "name": "注册角色-" + secrets.token_hex(6),
                "base_role": "operator",
                "permissions": ["stations.read", "companies.read", "companies.write"],
            },
        )
        assert role.status_code == 201
        profile_id = role.json()["id"]

        def body():
            key = secrets.token_hex(6)
            return {
                "username": "enroll-" + key,
                "password": password,
                "company_name": "注册企业-" + key,
                "credit_code": "A" + secrets.token_hex(9).upper()[:17],
                "contacts": [{"name": "测试联系人", "phone": "100001", "primary": True}],
            }

        first = body()
        r = applicant.post("/api/v1/enrollment", json=first)
        assert r.status_code == 201
        rid = r.json()["id"]
        ids.append(rid)
        eh = {"x-enrollment-csrf": r.json()["csrf_token"]}
        with SessionLocal() as db:
            assert not db.scalar(select(User.id).where(User.username == first["username"]))
        assert (
            applicant.post(
                "/api/v1/auth/login", json={"username": first["username"], "password": password}
            ).status_code
            == 401
        )
        assert (
            applicant.post("/api/v1/enrollment/me/submit", json={"version": 1}).status_code == 403
        )
        assert (
            applicant.post(
                "/api/v1/enrollment/me/submit", headers=eh, json={"version": 1}
            ).status_code
            == 422
        )
        file = applicant.post(
            "/api/v1/enrollment/me/attachments",
            headers=eh,
            files={"file": ("营业执照.pdf", b"%PDF-1.4\nexample", "application/pdf")},
        )
        assert file.status_code == 201
        mid = file.json()["id"]
        download = f"/api/v1/enrollment/me/attachments/{mid}/download"
        assert (
            applicant.post(
                "/api/v1/enrollment/me/submit", headers=eh, json={"version": 1}
            ).status_code
            == 200
        )
        assert (
            applicant.delete(f"/api/v1/enrollment/me/attachments/{mid}", headers=eh).status_code
            == 409
        )
        assert applicant.post("/api/v1/enrollment/logout", headers=eh).status_code == 200
        assert applicant.get("/api/v1/enrollment/me").status_code == 401
        assert (
            applicant.post(
                "/api/v1/enrollment/login",
                json={"username": first["username"], "password": "wrong-pass-123"},
            ).status_code
            == 401
        )
        resumed = applicant.post(
            "/api/v1/enrollment/login", json={"username": first["username"], "password": password}
        )
        assert resumed.status_code == 200 and resumed.json()["id"] == rid
        eh = {"x-enrollment-csrf": resumed.json()["csrf_token"]}
        second = other.post("/api/v1/enrollment", json=body())
        assert second.status_code == 201
        ids.append(second.json()["id"])
        assert other.get(download).status_code == 404
        detail = reg.get(f"/api/v1/registrations/{rid}")
        assert detail.status_code == 200
        assert reg.get(f"/api/v1/registrations/{rid}/attachments/{mid}/download").status_code == 200
        assert "password_hash" not in detail.json() and "csrf_token" not in detail.json()
        review = f"/api/v1/registrations/{rid}/review"
        assert (
            reg.post(
                review,
                headers=headers,
                json={"action": "reject", "version": 2, "comment": "补充资料"},
            ).status_code
            == 200
        )
        assert (
            applicant.delete(f"/api/v1/enrollment/me/attachments/{mid}", headers=eh).status_code
            == 409
        )
        assert (
            applicant.patch(
                "/api/v1/enrollment/me",
                headers=eh,
                json={**first, "version": 3, "registration_address": "补充地址"},
            ).status_code
            == 200
        )
        assert (
            applicant.post(
                "/api/v1/enrollment/me/submit", headers=eh, json={"version": 4}
            ).status_code
            == 200
        )
        assert (
            reg.post(
                review,
                headers=headers,
                json={
                    "action": "approve",
                    "version": 4,
                    "comment": "旧版本",
                    "role_profile_id": profile_id,
                    "role_profile_version": 1,
                },
            ).status_code
            == 409
        )
        approved = reg.post(
            review,
            headers=headers,
            json={
                "action": "approve",
                "version": 5,
                "comment": "演示核验通过",
                "role_profile_id": profile_id,
                "role_profile_version": 1,
            },
        )
        assert approved.status_code == 200
        data = approved.json()
        created_user = data["user_id"]
        created_company = data["operator_id"]
        assert data["status"] == "approved"
        assert (
            reg.post(
                review,
                headers=headers,
                json={
                    "action": "approve",
                    "version": 6,
                    "comment": "重复",
                    "role_profile_id": profile_id,
                    "role_profile_version": 1,
                },
            ).status_code
            == 409
        )
        signed = applicant.post(
            "/api/v1/auth/login", json={"username": first["username"], "password": password}
        )
        assert signed.status_code == 200
        assert applicant.get("/api/v1/auth/me").json()["operator_id"] == created_company
        assert applicant.get("/api/v1/subsidy/claims").status_code == 403
        assert applicant.get("/api/v1/enrollment/me").json()["status"] == "approved"
        company = reg.get(f"/api/v1/companies/{created_company}").json()
        assert (
            reg.post(
                f"/api/v1/companies/{created_company}/status",
                headers=headers,
                json={"enabled": False, "version": company["version"], "reason": "演示停用"},
            ).status_code
            == 200
        )
        assert applicant.get("/api/v1/enrollment/me").status_code == 401
        assert applicant.get("/api/v1/auth/me").status_code == 401
        with SessionLocal() as db:
            history = list(
                db.scalars(
                    select(RegistrationAction).where(RegistrationAction.registration_id == rid)
                )
            )
            assert all("password_hash" not in a.snapshot for a in history)
            assert next(a for a in history if a.action == "submit").snapshot["materials"][0][
                "sha256"
            ]
    finally:
        for c in clients:
            c.close()
        with SessionLocal.begin() as db:
            db.execute(delete(EnrollmentSession).where(EnrollmentSession.registration_id.in_(ids)))
            db.execute(
                delete(RegistrationAttachment).where(
                    RegistrationAttachment.registration_id.in_(ids)
                )
            )
            db.execute(
                delete(RegistrationAction).where(RegistrationAction.registration_id.in_(ids))
            )
            db.execute(
                delete(Audit).where(Audit.object_id.in_(ids), Audit.action.like("registration.%"))
            )
            db.execute(delete(Registration).where(Registration.id.in_(ids)))
            if created_user:
                db.execute(delete(LoginSession).where(LoginSession.user_id == created_user))
                db.execute(delete(User).where(User.id == created_user))
            if created_company:
                db.execute(
                    delete(Audit).where(
                        Audit.object_id == created_company, Audit.action.like("company.%")
                    )
                )
                db.execute(delete(Operator).where(Operator.id == created_company))
            if profile_id:
                db.execute(
                    delete(Audit).where(Audit.object_id == profile_id, Audit.action.like("role.%"))
                )
                db.execute(delete(RoleProfile).where(RoleProfile.id == profile_id))


def test_approval_username_conflict_rolls_back_company_creation(
    accounts, staff, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    rid = role_id = conflict_id = None
    key = secrets.token_hex(8)
    username = "conflict-" + key
    company = "冲突演示-" + key
    try:
        with SessionLocal.begin() as db:
            p = RoleProfile(
                name="conflict-role-" + key, base_role="operator", permissions=["stations.read"]
            )
            db.add(p)
            db.flush()
            role_id = p.id
        with (
            TestClient(app, client=("test-create-" + key, 1)) as portal,
            TestClient(app, client=("test-review-" + key, 1)) as reg,
        ):
            r = portal.post(
                "/api/v1/enrollment",
                json={
                    "company_name": company,
                    "credit_code": "A" + secrets.token_hex(9).upper()[:17],
                    "username": username,
                    "password": staff[1],
                    "contacts": [{"name": "测试", "phone": "100001"}],
                },
            )
            assert r.status_code == 201
            rid = r.json()["id"]
            eh = {"x-enrollment-csrf": r.json()["csrf_token"]}
            assert (
                portal.post(
                    "/api/v1/enrollment/me/attachments",
                    headers=eh,
                    files={"file": ("材料.pdf", b"%PDF-1.4\nexample", "application/pdf")},
                ).status_code
                == 201
            )
            assert (
                portal.post(
                    "/api/v1/enrollment/me/submit", headers=eh, json={"version": 1}
                ).status_code
                == 200
            )
            with SessionLocal.begin() as db:
                source = db.scalar(select(User).where(User.username == accounts[0][0]))
                conflict = User(
                    username=username,
                    password_hash=source.password_hash,
                    role="operator",
                    operator_id=source.operator_id,
                )
                db.add(conflict)
                db.flush()
                conflict_id = conflict.id
            login = reg.post(
                "/api/v1/auth/login", json={"username": staff[0][0], "password": staff[1]}
            )
            assert login.status_code == 200
            response = reg.post(
                f"/api/v1/registrations/{rid}/review",
                headers={"x-csrf-token": login.json()["csrf_token"]},
                json={
                    "version": 2,
                    "action": "approve",
                    "comment": "核验",
                    "role_profile_id": role_id,
                    "role_profile_version": 1,
                },
            )
            assert response.status_code == 409
            with SessionLocal() as db:
                assert not db.scalar(select(Operator.id).where(Operator.name == company))
                assert db.get(Registration, rid).status == "submitted"
                assert db.get(Registration, rid).user_id is None
    finally:
        with SessionLocal.begin() as db:
            if rid:
                db.execute(
                    delete(EnrollmentSession).where(EnrollmentSession.registration_id == rid)
                )
                db.execute(
                    delete(RegistrationAttachment).where(
                        RegistrationAttachment.registration_id == rid
                    )
                )
                db.execute(
                    delete(RegistrationAction).where(RegistrationAction.registration_id == rid)
                )
                db.execute(delete(Registration).where(Registration.id == rid))
            if conflict_id:
                db.execute(delete(User).where(User.id == conflict_id))
            if role_id:
                db.execute(delete(RoleProfile).where(RoleProfile.id == role_id))
