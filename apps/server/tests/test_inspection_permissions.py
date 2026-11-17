import secrets
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import (
    Audit,
    InspectionAction,
    InspectionAttachment,
    InspectionTask,
    LoginSession,
    RoleProfile,
    User,
    now,
)


def test_inspection_assignment_evidence_reply_return_and_close_are_independent(
    accounts, staff, business_login
):
    names, password, stations = accounts
    profiles, users, clients, tasks = [], [], [], []

    def login(name):
        c = TestClient(app, client=("inspection-grant-" + secrets.token_hex(8), 1))
        clients.append(c)
        r = business_login(c, name, password)
        assert r.status_code == 200
        return c, {"x-csrf-token": r.json()["csrf_token"]}

    root, rh = login(staff[0][0])
    with SessionLocal() as db:
        company = db.scalar(select(User.operator_id).where(User.username == names[0]))
    try:

        def actor(cap, base):
            p = root.post(
                "/api/v1/roles",
                headers=rh,
                json={
                    "name": "inspection-role-" + secrets.token_hex(8),
                    "base_role": base,
                    "permissions": ["stations.read", "inspections.read", cap],
                },
            )
            assert p.status_code == 201, p.text
            profiles.append(p.json()["id"])
            name = "inspection-grant-" + secrets.token_hex(8)
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
            assert u.status_code == 201, u.text
            users.append(u.json()["id"])
            return login(name)

        assigner, ah = actor("inspections.assign", "regulator")
        materials, mh = actor("inspections.materials", "operator")
        replier, ph = actor("inspections.reply", "operator")
        returner, th = actor("inspections.return", "regulator")
        closer, ch = actor("inspections.close", "regulator")
        body = {
            "station_id": stations[0],
            "title": "岗位分离验证",
            "description": "隐患整改",
            "severity": "major",
            "due_at": (now() + timedelta(days=3)).isoformat(),
        }
        assert closer.post("/api/v1/inspections", headers=ch, json=body).status_code == 403
        created = assigner.post("/api/v1/inspections", headers=ah, json=body)
        assert created.status_code == 201
        tid = created.json()["id"]
        tasks.append(tid)
        url = f"/api/v1/inspections/{tid}"
        document = {"file": ("proof.pdf", b"%PDF-1.4\nfixture", "application/pdf")}
        assert replier.post(url + "/attachments", headers=ph, files=document).status_code == 403
        uploaded = materials.post(url + "/attachments", headers=mh, files=document)
        assert uploaded.status_code == 201
        mid = uploaded.json()["id"]
        assert returner.get(url + f"/attachments/{mid}/download").status_code == 200
        assert replier.delete(url + f"/attachments/{mid}", headers=ph).status_code == 403

        def action(c, h, verb, version):
            return c.post(
                url + "/actions",
                headers=h,
                json={
                    "action": verb,
                    "version": version,
                    "comment": "整改证据与复核依据",
                    **(
                        {
                            "contact": {"name": "Executor", "phone": "13800000001"},
                            "performed_at": (now() - timedelta(days=1)).isoformat(),
                        }
                        if verb == "reply"
                        else {}
                    ),
                },
            )

        assert action(materials, mh, "reply", 1).status_code == 403
        assert action(replier, ph, "reply", 1).status_code == 200
        assert materials.delete(url + f"/attachments/{mid}", headers=mh).status_code == 409
        assert action(returner, th, "close", 2).status_code == 403
        assert action(closer, ch, "return", 2).status_code == 403
        assert action(returner, th, "return", 2).status_code == 200
        # Historical evidence stays frozen even after a task is returned.
        assert materials.delete(url + f"/attachments/{mid}", headers=mh).status_code == 409
        fresh = materials.post(url + "/attachments", headers=mh, files=document)
        assert fresh.status_code == 201
        assert (
            materials.delete(url + f"/attachments/{fresh.json()['id']}", headers=mh).status_code
            == 200
        )
        # Previously bound evidence alone cannot justify a new reply.
        assert action(replier, ph, "reply", 3).status_code == 422
        assert materials.post(url + "/attachments", headers=mh, files=document).status_code == 201
        assert action(replier, ph, "reply", 3).status_code == 200
        assert action(assigner, ah, "close", 4).status_code == 403
        assert action(closer, ch, "close", 4).status_code == 200
        assert action(replier, ph, "reply", 5).status_code == 409
        assert action(closer, ch, "unknown", 5).status_code == 422
        for permissions, base, status in [
            (["stations.read", "inspections.reply"], "operator", 422),
            (["stations.read", "inspections.read", "inspections.close"], "operator", 422),
            (["stations.read", "inspections.read", "inspections.write"], "operator", 201),
        ]:
            response = root.post(
                "/api/v1/roles",
                headers=rh,
                json={
                    "name": "inspection-role-" + secrets.token_hex(8),
                    "base_role": base,
                    "permissions": permissions,
                },
            )
            assert response.status_code == status, response.text
            if status == 201:
                profiles.append(response.json()["id"])
                assert set(response.json()["permissions"]) == {
                    "stations.read",
                    "inspections.read",
                    "inspections.materials",
                    "inspections.reply",
                }
        with SessionLocal() as db:
            logs = list(
                db.scalars(
                    select(Audit)
                    .where(
                        Audit.object_id == tid,
                        Audit.action.in_(
                            [
                                "inspections.assign",
                                "inspections.reply",
                                "inspections.return",
                                "inspections.close",
                            ]
                        ),
                    )
                    .order_by(Audit.id)
                )
            )
            assert [log.action for log in logs] == [
                "inspections.assign",
                "inspections.reply",
                "inspections.return",
                "inspections.reply",
                "inspections.close",
            ]
            assert logs[-1].detail["before"]["status"] == "replied"
            assert logs[-1].detail["after"]["status"] == "closed"
    finally:
        for c in clients:
            c.close()
        with SessionLocal.begin() as db:
            for item in db.scalars(
                select(InspectionAttachment).where(InspectionAttachment.task_id.in_(tasks))
            ):
                (Path(settings.storage_path).resolve() / item.storage_key).unlink(missing_ok=True)
            db.execute(delete(InspectionAttachment).where(InspectionAttachment.task_id.in_(tasks)))
            db.execute(delete(InspectionAction).where(InspectionAction.task_id.in_(tasks)))
            db.execute(delete(InspectionTask).where(InspectionTask.id.in_(tasks)))
            db.execute(delete(LoginSession).where(LoginSession.user_id.in_(users)))
            db.execute(delete(Audit).where(Audit.user_id.in_(users)))
            db.execute(delete(User).where(User.id.in_(users)))
            db.execute(delete(RoleProfile).where(RoleProfile.id.in_(profiles)))
