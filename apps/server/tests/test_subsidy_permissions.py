import secrets
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.main import app
from app.models import (
    Audit,
    EnergyAllocation,
    EnergyRecord,
    LoginSession,
    RoleProfile,
    Station,
    SubsidyAction,
    SubsidyAttachment,
    SubsidyClaim,
    SubsidyPolicy,
    User,
)


def test_separate_subsidy_actions_and_bounded_legacy_delegation(
    accounts, staff, monkeypatch, tmp_path, business_login
):
    from app.config import settings

    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    names, password, stations = accounts
    profiles = []
    users = []
    clients = []
    pid = None

    def client(username):
        c = TestClient(app, client=("fine-subsidy-" + secrets.token_hex(8), 1))
        clients.append(c)
        r = business_login(c, username, password)
        assert r.status_code == 200
        return c, {"x-csrf-token": r.json()["csrf_token"]}

    reg, rh = client(staff[0][0])
    with SessionLocal.begin() as db:
        company = db.scalar(select(User.operator_id).where(User.username == names[0]))
        db.get(Station, stations[0]).status = "operating"
        energy = EnergyRecord(
            station_id=stations[0],
            source="fine-test",
            external_id=secrets.token_hex(8),
            day=date(2026, 11, 1),
            kwh="42",
            eligible=True,
        )
        db.add(energy)
        db.flush()
        eid = energy.id
    try:

        def role(base, grants):
            r = reg.post(
                "/api/v1/roles",
                headers=rh,
                json={
                    "name": "fine-" + secrets.token_hex(8),
                    "base_role": base,
                    "permissions": ["stations.read", "subsidy.read", *grants],
                },
            )
            assert r.status_code == 201
            profiles.append(r.json()["id"])
            return r.json()

        def actor(base, grants):
            p = role(base, grants)
            name = "fine-" + secrets.token_hex(8)
            r = reg.post(
                "/api/v1/accounts",
                headers=rh,
                json={
                    "username": name,
                    "password": password,
                    "role_profile_id": p["id"],
                    "operator_id": company if base == "operator" else None,
                },
            )
            assert r.status_code == 201
            users.append(r.json()["id"])
            c, h = client(name)
            return c, h, p, name

        reviewer, vh, profile, review_name = actor("regulator", ["subsidy.review", "roles.manage"])
        adjuster, ah, _, _ = actor("regulator", ["subsidy.adjust"])
        payer, ph, _, _ = actor("regulator", ["subsidy.disburse"])
        creator, ch, _, _ = actor("operator", ["subsidy.create"])
        submitter, sh, _, _ = actor("operator", ["subsidy.submit"])
        legacy = role("operator", ["subsidy.write"])
        assert "subsidy.write" not in legacy["permissions"]
        assert set(legacy["permissions"]) == {
            "stations.read",
            "subsidy.read",
            "subsidy.create",
            "subsidy.submit",
            "subsidy.withdraw",
        }
        policy = {
            "code": "fine-" + secrets.token_hex(8),
            "version": 1,
            "name": "动作隔离演示",
            "rate": "0.1",
            "effective_from": "2026-01-01",
            "effective_to": "2026-12-31",
        }
        assert reviewer.post("/api/v1/subsidy/policies", headers=vh, json=policy).status_code == 403
        r = reg.post("/api/v1/subsidy/policies", headers=rh, json=policy)
        assert r.status_code == 201
        pid = r.json()["id"]
        body = {
            "station_id": stations[0],
            "policy_id": pid,
            "start_date": "2026-11-01",
            "end_date": "2026-11-30",
        }
        assert submitter.post("/api/v1/subsidy/claims", headers=sh, json=body).status_code == 403
        r = creator.post("/api/v1/subsidy/claims", headers=ch, json=body)
        assert r.status_code == 201
        claim = r.json()
        from subsidy_samples import evidence

        with SessionLocal.begin() as db:
            owner = db.scalar(select(User).where(User.username == names[0]))
            evidence(db, db.get(SubsidyClaim, claim["id"]), owner)

        url = f"/api/v1/subsidy/claims/{claim['id']}/actions"

        def action(c, h, verb, version, delta="0"):
            return c.post(
                url,
                headers=h,
                json={
                    "action": verb,
                    "version": version,
                    "comment": "动作隔离演示依据",
                    "delta": delta,
                },
            )

        assert action(creator, ch, "submit", 1).status_code == 403
        assert action(submitter, sh, "submit", 1).status_code == 200
        assert action(submitter, sh, "withdraw", 2).status_code == 403
        assert action(reviewer, vh, "adjust", 2, "1.01").status_code == 403
        assert action(adjuster, ah, "approve", 2).status_code == 403
        assert action(adjuster, ah, "adjust", 2, "1.01").status_code == 200
        approved = action(reviewer, vh, "approve", 3, "999")
        assert approved.status_code == 200 and Decimal(
            approved.json()["adjusted_amount"]
        ) == Decimal("5.21")
        assert action(reviewer, vh, "record_disbursement", 4).status_code == 403
        assert action(payer, ph, "record_disbursement", 4).status_code == 200
        denied = reviewer.post(
            "/api/v1/roles",
            headers=vh,
            json={
                "name": "denied-" + secrets.token_hex(8),
                "base_role": "operator",
                "permissions": ["stations.read", "subsidy.read", "subsidy.write"],
            },
        )
        assert denied.status_code == 403
        missing_read = reg.post(
            "/api/v1/roles",
            headers=rh,
            json={
                "name": "denied-" + secrets.token_hex(8),
                "base_role": "regulator",
                "permissions": ["stations.read", "subsidy.adjust"],
            },
        )
        assert missing_read.status_code == 422
        changed = reg.patch(
            f"/api/v1/roles/{profile['id']}",
            headers=rh,
            json={**profile, "permissions": ["stations.read", "subsidy.read", "roles.manage"]},
        )
        assert changed.status_code == 200 and reviewer.get("/api/v1/auth/me").status_code == 401
        fresh, fh = client(review_name)
        assert action(fresh, fh, "approve", 5).status_code == 403
    finally:
        for c in clients:
            c.close()
        with SessionLocal.begin() as db:
            if pid:
                claims = select(SubsidyClaim.id).where(SubsidyClaim.policy_id == pid)
                db.execute(delete(SubsidyAttachment).where(SubsidyAttachment.claim_id.in_(claims)))
                db.execute(delete(EnergyAllocation).where(EnergyAllocation.claim_id.in_(claims)))
                db.execute(delete(SubsidyAction).where(SubsidyAction.claim_id.in_(claims)))
                db.execute(delete(SubsidyClaim).where(SubsidyClaim.policy_id == pid))
                db.execute(delete(SubsidyPolicy).where(SubsidyPolicy.id == pid))
            db.execute(delete(EnergyRecord).where(EnergyRecord.id == eid))
            db.execute(delete(LoginSession).where(LoginSession.user_id.in_(users)))
            db.execute(delete(Audit).where(Audit.user_id.in_(users)))
            db.execute(delete(User).where(User.id.in_(users)))
            db.execute(delete(RoleProfile).where(RoleProfile.id.in_(profiles)))
