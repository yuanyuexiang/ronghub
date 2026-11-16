import secrets
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Attachment, Audit, LoginSession, RoleProfile, User


def test_application_actions_and_materials_are_independently_authorized(
    accounts, staff, business_login
):
    names, password, stations = accounts
    profiles = []
    users = []
    clients = []

    def login(name):
        c = TestClient(app, client=("application-grant-" + secrets.token_hex(8), 1))
        clients.append(c)
        r = business_login(c, name, password)
        assert r.status_code == 200
        return c, {"x-csrf-token": r.json()["csrf_token"]}

    root, rh = login(staff[0][0])
    with SessionLocal() as db:
        company = db.scalar(select(User.operator_id).where(User.username == names[0]))
    try:

        def actor(cap, base="operator"):
            p = root.post(
                "/api/v1/roles",
                headers=rh,
                json={
                    "name": "app-role-" + secrets.token_hex(8),
                    "base_role": base,
                    "permissions": ["stations.read", "applications.read", cap],
                },
            )
            assert p.status_code == 201
            profiles.append(p.json()["id"])
            name = "app-grant-" + secrets.token_hex(8)
            u = root.post(
                "/api/v1/accounts",
                headers=rh,
                json={
                    "username": name,
                    "password": password,
                    "role_profile_id": profiles[-1],
                    "operator_id": company if base == "operator" else None,
                },
            )
            assert u.status_code == 201
            users.append(u.json()["id"])
            return login(name)

        creator, ch = actor("applications.create")
        editor, eh = actor("applications.edit")
        materials, mh = actor("applications.materials")
        submitter, sh = actor("applications.submit")
        withdrawer, wh = actor("applications.withdraw")
        reviewer, vh = actor("applications.review", "regulator")
        body = {"station_id": stations[0], "kind": "access", "payload": {"note": "拆分演示"}}
        assert editor.post("/api/v1/applications", headers=eh, json=body).status_code == 403
        created = creator.post("/api/v1/applications", headers=ch, json=body)
        assert created.status_code == 201
        aid = created.json()["id"]
        url = f"/api/v1/applications/{aid}"
        assert (
            creator.patch(url, headers=ch, json={"version": 1, "note": "越权"}).status_code == 403
        )
        assert (
            editor.patch(url, headers=eh, json={"version": 1, "note": "已补正"}).status_code == 200
        )
        document = {"file": ("proof.pdf", b"%PDF-1.4\nfixture", "application/pdf")}
        assert editor.post(url + "/attachments", headers=eh, files=document).status_code == 403
        upload = materials.post(url + "/attachments", headers=mh, files=document)
        assert upload.status_code == 201
        material_id = upload.json()["id"]
        assert creator.delete(f"/api/v1/attachments/{material_id}", headers=ch).status_code == 403
        assert editor.get(f"/api/v1/attachments/{material_id}/download").status_code == 200
        assert materials.delete(f"/api/v1/attachments/{material_id}", headers=mh).status_code == 200
        upload = materials.post(url + "/attachments", headers=mh, files=document)
        assert upload.status_code == 201
        material_id = upload.json()["id"]

        def action(c, h, verb, version):
            return c.post(
                url + "/actions",
                headers=h,
                json={"action": verb, "version": version, "comment": "权限拆分验证"},
            )

        assert action(creator, ch, "submit", 2).status_code == 403
        assert action(submitter, sh, "submit", 2).status_code == 200
        assert materials.delete(f"/api/v1/attachments/{material_id}", headers=mh).status_code == 409
        assert action(submitter, sh, "withdraw", 3).status_code == 403
        assert action(withdrawer, wh, "withdraw", 3).status_code == 200
        again = creator.post("/api/v1/applications", headers=ch, json=body)
        assert again.status_code == 201
        aid = again.json()["id"]
        url = f"/api/v1/applications/{aid}"
        assert action(submitter, sh, "submit", 1).status_code == 200
        assert action(editor, eh, "approve", 2).status_code == 403
        assert action(reviewer, vh, "approve", 2).status_code == 200
        legacy = root.post(
            "/api/v1/roles",
            headers=rh,
            json={
                "name": "legacy-app-" + secrets.token_hex(8),
                "base_role": "operator",
                "permissions": ["stations.read", "applications.read", "applications.write"],
            },
        )
        assert legacy.status_code == 201
        profiles.append(legacy.json()["id"])
        assert (
            "applications.write" not in legacy.json()["permissions"]
            and "applications.review" not in legacy.json()["permissions"]
        )
        assert set(legacy.json()["permissions"]) == {
            "stations.read",
            "applications.read",
            "applications.create",
            "applications.edit",
            "applications.materials",
            "applications.submit",
            "applications.withdraw",
        }
        assert (
            root.post(
                "/api/v1/roles",
                headers=rh,
                json={
                    "name": "bad-" + secrets.token_hex(8),
                    "base_role": "regulator",
                    "permissions": ["stations.read", "applications.materials"],
                },
            ).status_code
            == 422
        )
        with SessionLocal() as db:
            log = db.scalar(
                select(Audit).where(Audit.object_id == aid, Audit.action == "applications.approve")
            )
            assert log.detail["before"]["status"] == "submitted"
            assert log.detail["after"]["status"] == "approved"
            assert log.detail["before"]["station_status"] == "pending"
            assert log.detail["after"]["station_status"] == "access_approved"
    finally:
        for c in clients:
            c.close()
        with SessionLocal.begin() as db:
            records = list(db.scalars(select(Attachment).where(Attachment.uploaded_by.in_(users))))
            for item in records:
                (Path(settings.storage_path).resolve() / item.storage_key).unlink(missing_ok=True)
                db.delete(item)
            db.execute(delete(LoginSession).where(LoginSession.user_id.in_(users)))
            db.execute(delete(Audit).where(Audit.user_id.in_(users)))
            # Accounts fixture removes application histories later; remove actor references first.
            from app.models import ApprovalAction, StationApplication

            db.execute(delete(ApprovalAction).where(ApprovalAction.actor_id.in_(users)))
            db.execute(delete(StationApplication).where(StationApplication.applicant_id.in_(users)))
            db.execute(delete(User).where(User.id.in_(users)))
            db.execute(delete(RoleProfile).where(RoleProfile.id.in_(profiles)))
