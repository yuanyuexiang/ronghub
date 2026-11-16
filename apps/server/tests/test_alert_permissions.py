import secrets
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.main import app
from app.models import (
    AlertCursor,
    AlertEvent,
    AlertNotice,
    AlertRule,
    Audit,
    LoginSession,
    RoleProfile,
    User,
    now,
)


def test_alert_rules_samples_and_acknowledgement_have_independent_permissions(
    accounts, staff, business_login
):
    _, password, stations = accounts
    profiles, users, clients, rules = [], [], [], []

    def login(name):
        c = TestClient(app, client=("alert-grant-" + secrets.token_hex(8), 1))
        clients.append(c)
        r = business_login(c, name, password)
        assert r.status_code == 200
        return c, {"x-csrf-token": r.json()["csrf_token"]}

    root, rh = login(staff[0][0])
    try:

        def actor(cap, districts=None):
            p = root.post(
                "/api/v1/roles",
                headers=rh,
                json={
                    "name": "alert-role-" + secrets.token_hex(8),
                    "base_role": "regulator",
                    "permissions": ["stations.read", "alerts.read", cap],
                },
            )
            assert p.status_code == 201, p.text
            profiles.append(p.json()["id"])
            name = "alert-grant-" + secrets.token_hex(8)
            u = root.post(
                "/api/v1/accounts",
                headers=rh,
                json={
                    "username": name,
                    "password": password,
                    "role_profile_id": profiles[-1],
                    "allowed_districts": districts,
                },
            )
            assert u.status_code == 201, u.text
            users.append(u.json()["id"])
            return login(name)

        maker, mh = actor("alerts.rules")
        observer, oh = actor("alerts.samples")
        confirmer, ch = actor("alerts.acknowledge")
        regional, gh = actor("alerts.rules", ["测试"])
        outside, xh = actor("alerts.samples", ["外区"])
        rule = {
            "code": "grant-" + secrets.token_hex(8),
            "version": 1,
            "name": "权限隔离演示",
            "metric": "offline_minutes",
            "unit": "分钟",
            "trigger_value": "30",
            "recover_value": "5",
        }
        for c, h in [(observer, oh), (confirmer, ch), (regional, gh)]:
            assert c.post("/api/v1/alerts/rules", headers=h, json=rule).status_code == 403
        created = maker.post("/api/v1/alerts/rules", headers=mh, json=rule)
        assert created.status_code == 201
        rid = created.json()["id"]
        rules.append(rid)
        sample = {
            "station_id": stations[0],
            "rule_id": rid,
            "target": "permission-target",
            "value": "40",
            "observed_at": (now() - timedelta(minutes=2)).isoformat(),
        }
        for c, h in [(maker, mh), (confirmer, ch)]:
            assert c.post("/api/v1/alerts/samples", headers=h, json=sample).status_code == 403
        assert outside.post("/api/v1/alerts/samples", headers=xh, json=sample).status_code == 404
        opened = observer.post("/api/v1/alerts/samples", headers=oh, json=sample)
        assert opened.status_code == 200 and opened.json()["outcome"] == "opened"
        eid = opened.json()["event_id"]
        url = f"/api/v1/alerts/{eid}"
        for c, h in [(maker, mh), (observer, oh)]:
            assert (
                c.post(
                    url + "/acknowledge", headers=h, json={"version": 1, "note": "越权"}
                ).status_code
                == 403
            )
        confirmed = confirmer.post(
            url + "/acknowledge",
            headers=ch,
            json={"version": 1, "note": "已核查，持续关注设备恢复"},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["status"] == "active" and confirmed.json()["acknowledged_at"]
        assert (
            confirmer.post(
                url + "/acknowledge", headers=ch, json={"version": 1, "note": "重复"}
            ).status_code
            == 409
        )
        recovered = observer.post(
            "/api/v1/alerts/samples",
            headers=oh,
            json={
                **sample,
                "value": "0",
                "observed_at": now().isoformat(),
            },
        )
        assert recovered.status_code == 200 and recovered.json()["outcome"] == "recovered"
        saved = confirmer.get(url).json()
        assert saved["status"] == "recovered" and saved["acknowledged_at"]
        for caps, base, status in [
            (["stations.read", "alerts.acknowledge"], "regulator", 422),
            (["stations.read", "alerts.read", "alerts.acknowledge"], "operator", 422),
            (["stations.read", "alerts.read", "alerts.write"], "operator", 422),
            (["stations.read", "alerts.read", "alerts.write"], "regulator", 201),
        ]:
            r = root.post(
                "/api/v1/roles",
                headers=rh,
                json={
                    "name": "alert-role-" + secrets.token_hex(8),
                    "base_role": base,
                    "permissions": caps,
                },
            )
            assert r.status_code == status, r.text
            if status == 201:
                profiles.append(r.json()["id"])
                assert set(r.json()["permissions"]) == {
                    "stations.read",
                    "alerts.read",
                    "alerts.rules",
                    "alerts.samples",
                    "alerts.acknowledge",
                }
        with SessionLocal() as db:
            log = db.scalar(
                select(Audit).where(Audit.object_id == eid, Audit.action == "alerts.acknowledge")
            )
            assert log.detail["before"]["acknowledged_at"] is None
            assert log.detail["after"]["acknowledged_by"] is not None
            assert log.detail["before"]["status"] == log.detail["after"]["status"] == "active"
    finally:
        for c in clients:
            c.close()
        with SessionLocal.begin() as db:
            events = select(AlertEvent.id).where(AlertEvent.rule_id.in_(rules))
            db.execute(delete(AlertCursor).where(AlertCursor.rule_id.in_(rules)))
            db.execute(delete(AlertNotice).where(AlertNotice.event_id.in_(events)))
            db.execute(delete(AlertEvent).where(AlertEvent.rule_id.in_(rules)))
            db.execute(delete(AlertRule).where(AlertRule.id.in_(rules)))
            db.execute(delete(LoginSession).where(LoginSession.user_id.in_(users)))
            db.execute(delete(Audit).where(Audit.user_id.in_(users)))
            db.execute(delete(User).where(User.id.in_(users)))
            db.execute(delete(RoleProfile).where(RoleProfile.id.in_(profiles)))
