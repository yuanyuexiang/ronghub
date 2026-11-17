import hashlib
import secrets
from datetime import date, timedelta
from decimal import Decimal

import pytest
from alert_notification_samples import remove_for_events
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import alerts, inspection, rating, subsidy, workflows
from app.db import SessionLocal
from app.main import app
from app.models import (
    AlertCursor,
    AlertEvent,
    AlertNotice,
    AlertRule,
    ApprovalAction,
    Attachment,
    Audit,
    EnergyAllocation,
    EnergyRecord,
    InspectionAction,
    InspectionTask,
    Operator,
    RatingAction,
    RatingApplication,
    RatingStandard,
    Station,
    StationApplication,
    SubsidyAction,
    SubsidyClaim,
    SubsidyPolicy,
    User,
    now,
)


@pytest.fixture()
def regional_data(accounts, staff, tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    key = secrets.token_hex(8)
    with SessionLocal.begin() as db:
        owners = [db.scalar(select(User).where(User.username == n)) for n in accounts[0]]
        reg = db.scalar(select(User).where(User.username == staff[0][0]))
        assoc = db.scalar(select(User).where(User.username == staff[0][1]))
        stations = [db.get(Station, sid) for sid in accounts[2]]
        stations[0].district = "范围甲"
        stations[1].district = "范围乙"
        for owner, district in [(owners[0], "范围乙"), (owners[1], "范围甲")]:
            s = Station(
                operator_id=owner.operator_id,
                source="test-district",
                external_id=secrets.token_hex(8),
                name="区域测试-" + key + "-" + district,
                address="测试",
                district=district,
                status="operating",
                power_kw=10,
                connectors=1,
            )
            db.add(s)
            stations.append(s)
        for s in stations:
            s.status = "operating"
        extra = Operator(name="区域外企业-" + key)
        db.add(extra)
        policy = SubsidyPolicy(
            code="district-" + key,
            version=1,
            name="区域示例",
            rate=Decimal("0.1"),
            effective_from=date(2026, 1, 1),
            effective_to=date(2026, 12, 31),
        )
        db.add(policy)
        standard = RatingStandard(
            code="district-" + key,
            version=1,
            name="区域示例",
            rules={
                "indicators": [
                    {
                        "code": "service",
                        "name": "服务",
                        "weight": "100",
                        "max_score": "100",
                        "source": "manual",
                    }
                ],
                "thresholds": [{"minimum": "60", "stars": 3}],
            },
        )
        db.add(standard)
        rule = AlertRule(
            code="district-" + key,
            version=1,
            name="区域告警",
            metric="demo",
            unit="分钟",
            trigger_value=30,
            recover_value=5,
        )
        db.add(rule)
        db.flush()
        ids = [s.id for s in stations]
        aids = []
        cids = []
        rids = []
        tids = []
        eids = []
        files = []
        for i, (s, kwh) in enumerate(zip(stations, ["10", "99", "90", "20"], strict=True)):
            owner = owners[0] if i in {0, 2} else owners[1]
            a = workflows.create(db, owner, s.id, "change", {"note": "区域测试"})
            aids.append(a.id)
            data = b"%PDF-1.4\nScope evidence"
            storage_key = secrets.token_hex(24)
            (tmp_path / storage_key).write_bytes(data)
            material = Attachment(
                application_id=a.id,
                uploaded_by=owner.id,
                filename="证据.pdf",
                storage_key=storage_key,
                content_type="application/pdf",
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
            )
            db.add(material)
            db.flush()
            files.append(material.id)
            db.add(
                EnergyRecord(
                    station_id=s.id,
                    source="test-district",
                    external_id=secrets.token_hex(8),
                    day=date(2026, 11, 1),
                    kwh=Decimal(kwh),
                )
            )
            db.flush()
            cids.append(
                subsidy.create_claim(
                    db, owner, s.id, policy.id, date(2026, 11, 1), date(2026, 11, 1)
                ).id
            )
            rids.append(rating.create(db, owner, s.id, standard.id, {"service": "90"}, "测试").id)
            tids.append(
                inspection.create(
                    db, reg, s.id, "测试巡检", "测试", "normal", now() + timedelta(days=1)
                ).id
            )
            eids.append(
                alerts.observe(
                    db, reg, s.id, rule.id, "test-target", Decimal(40), now() - timedelta(minutes=1)
                )["event_id"]
            )
        reg.allowed_districts = ["范围甲"]
        assoc.allowed_districts = ["范围甲"]
        owners[0].allowed_districts = ["范围甲"]
        oid = owners[0].operator_id
        regid = reg.id
        extraid = extra.id
        pid = policy.id
        stdid = standard.id
        ruleid = rule.id
    yield {
        "stations": ids,
        "apps": aids,
        "claims": cids,
        "ratings": rids,
        "tasks": tids,
        "alerts": eids,
        "files": files,
        "operator_id": oid,
        "outside_company": extraid,
        "rule_id": ruleid,
    }
    with SessionLocal.begin() as db:
        db.execute(delete(Attachment).where(Attachment.application_id.in_(aids)))
        db.execute(delete(ApprovalAction).where(ApprovalAction.application_id.in_(aids)))
        db.execute(delete(StationApplication).where(StationApplication.id.in_(aids)))
        db.execute(delete(SubsidyAction).where(SubsidyAction.claim_id.in_(cids)))
        db.execute(delete(EnergyAllocation).where(EnergyAllocation.claim_id.in_(cids)))
        db.execute(delete(SubsidyClaim).where(SubsidyClaim.id.in_(cids)))
        db.execute(delete(EnergyRecord).where(EnergyRecord.station_id.in_(ids)))
        db.execute(delete(RatingAction).where(RatingAction.application_id.in_(rids)))
        db.execute(delete(RatingApplication).where(RatingApplication.id.in_(rids)))
        db.execute(delete(InspectionAction).where(InspectionAction.task_id.in_(tids)))
        db.execute(delete(InspectionTask).where(InspectionTask.id.in_(tids)))
        db.execute(delete(AlertCursor).where(AlertCursor.rule_id == ruleid))
        remove_for_events(db, eids)
        db.execute(delete(AlertNotice).where(AlertNotice.event_id.in_(eids)))
        db.execute(delete(AlertEvent).where(AlertEvent.id.in_(eids)))
        db.execute(delete(AlertRule).where(AlertRule.id == ruleid))
        db.execute(delete(Station).where(Station.id.in_(ids[2:])))
        db.execute(delete(SubsidyPolicy).where(SubsidyPolicy.id == pid))
        db.execute(delete(RatingStandard).where(RatingStandard.id == stdid))
        db.execute(delete(Operator).where(Operator.id == extraid))
        db.execute(delete(Audit).where(Audit.user_id == regid, Audit.action == "statistics.export"))


def test_regional_station_objects_actions_materials_and_exports(regional_data, accounts, staff):
    d = regional_data
    clients = [TestClient(app, client=("scope-" + secrets.token_hex(6), 1)) for _ in range(3)]
    try:
        reg, op, assoc = clients
        headers = []
        for client, name in zip(clients, [staff[0][0], accounts[0][0], staff[0][1]], strict=True):
            r = client.post("/api/v1/auth/login", json={"username": name, "password": accounts[1]})
            assert r.status_code == 200
            headers.append({"x-csrf-token": r.json()["csrf_token"]})
        assert {s["id"] for s in reg.get("/api/v1/stations").json()["items"]} == {
            d["stations"][0],
            d["stations"][3],
        }
        assert [s["id"] for s in op.get("/api/v1/stations").json()["items"]] == [d["stations"][0]]
        assert reg.get("/api/v1/dashboard").json()["stations"] == 2
        for index in [1, 2]:
            sid = d["stations"][index]
            assert reg.get(f"/api/v1/stations/{sid}").status_code == 404
            assert reg.get(f"/api/v1/stations/{sid}/equipment").status_code == 404
            assert reg.get(f"/api/v1/applications/{d['apps'][index]}").status_code == 404
            assert (
                reg.get(f"/api/v1/applications/{d['apps'][index]}/attachments").status_code == 404
            )
            assert reg.get(f"/api/v1/attachments/{d['files'][index]}/download").status_code == 404
            assert reg.get(f"/api/v1/subsidy/claims/{d['claims'][index]}").status_code == 404
            assert (
                assoc.get(f"/api/v1/ratings/applications/{d['ratings'][index]}").status_code == 404
            )
            assert reg.get(f"/api/v1/inspections/{d['tasks'][index]}").status_code == 404
            assert reg.get(f"/api/v1/alerts/{d['alerts'][index]}").status_code == 404
            assert (
                reg.post(
                    f"/api/v1/applications/{d['apps'][index]}/actions",
                    headers=headers[0],
                    json={"action": "approve", "version": 1, "comment": "越区"},
                ).status_code
                == 404
            )
            assert (
                reg.post(
                    f"/api/v1/subsidy/claims/{d['claims'][index]}/actions",
                    headers=headers[0],
                    json={"action": "approve", "version": 1, "comment": "越区"},
                ).status_code
                == 404
            )
        assert len(reg.get("/api/v1/applications").json()["items"]) == 2
        assert len(reg.get("/api/v1/subsidy/claims").json()["items"]) == 2
        assert len(assoc.get("/api/v1/ratings/applications").json()["items"]) == 2
        assert len(reg.get("/api/v1/inspections").json()["items"]) == 2
        assert len(reg.get("/api/v1/alerts").json()["items"]) == 2
        assert reg.get(f"/api/v1/companies/{d['outside_company']}").status_code == 404
        assert d["outside_company"] not in [
            o["id"] for o in reg.get("/api/v1/operators").json()["items"]
        ]
        assert (
            reg.get("/api/v1/statistics?start_date=2026-11-01&end_date=2026-11-02").json()[
                "total_kwh"
            ]
            == "30.000000"
        )
        export = reg.get("/api/v1/statistics/export?start_date=2026-11-01&end_date=2026-11-02")
        assert export.status_code == 200
        assert "99.000000" not in export.text and "90.000000" not in export.text
        assert reg.get("/api/v1/sync/tasks").status_code == 403
        assert reg.get("/api/v1/registrations").status_code == 403
        assert reg.get("/api/v1/content?manage=true").status_code == 403
        assert (
            reg.post(
                f"/api/v1/companies/{d['operator_id']}/status",
                headers=headers[0],
                json={"enabled": False, "version": 1, "reason": "越界"},
            ).status_code
            == 403
        )
        assert (
            op.post(
                "/api/v1/applications",
                headers=headers[1],
                json={"station_id": d["stations"][2], "kind": "change"},
            ).status_code
            == 404
        )
        assert op.get(f"/api/v1/stations/{d['stations'][3]}").status_code == 404
        with SessionLocal.begin() as db:
            db.scalar(select(User).where(User.username == accounts[0][0])).allowed_districts = []
        assert op.get("/api/v1/stations").json()["total"] == 0
        assert op.get("/api/v1/subsidy/claims").json()["items"] == []
        empty_summary = op.get("/api/v1/dashboard").json()
        assert empty_summary["stations"] == 0
        assert empty_summary["energy_total"] is None
    finally:
        for c in clients:
            c.close()


def test_regional_admin_can_only_delegate_subset_scopes(accounts, staff):
    from app.models import LoginSession, RoleProfile

    roleid = None
    created = []
    key = secrets.token_hex(6)
    try:
        with SessionLocal.begin() as db:
            issuer = db.scalar(select(User).where(User.username == staff[0][0]))
            issuer.allowed_districts = ["范围甲"]
            issuerid = issuer.id
            target = db.scalar(select(User).where(User.username == accounts[0][0]))
            globalid = target.id
            profile = RoleProfile(
                name="region-grant-" + key,
                base_role="regulator",
                permissions=["stations.read", "accounts.manage"],
            )
            db.add(profile)
            db.flush()
            roleid = profile.id
        with TestClient(app, client=("region-grant-" + key, 1)) as reg:
            signed = reg.post(
                "/api/v1/auth/login", json={"username": staff[0][0], "password": accounts[1]}
            )
            assert signed.status_code == 200
            h = {"x-csrf-token": signed.json()["csrf_token"]}
            body = {
                "username": "region-child-" + key,
                "password": accounts[1],
                "role_profile_id": roleid,
                "allowed_districts": ["范围甲"],
            }
            for denied in [None, ["范围乙"], ["范围甲", "范围乙"]]:
                assert (
                    reg.post(
                        "/api/v1/accounts", headers=h, json={**body, "allowed_districts": denied}
                    ).status_code
                    == 403
                )
            child = reg.post("/api/v1/accounts", headers=h, json=body)
            assert child.status_code == 201
            cid = child.json()["id"]
            created.append(cid)
            assert child.json()["allowed_districts"] == ["范围甲"]
            visible = reg.get("/api/v1/accounts").json()["items"]
            assert cid in [u["id"] for u in visible] and globalid not in [u["id"] for u in visible]
            for denied in [None, ["范围乙"]]:
                assert (
                    reg.patch(
                        f"/api/v1/accounts/{cid}",
                        headers=h,
                        json={
                            "version": 1,
                            "role_profile_id": roleid,
                            "enabled": True,
                            "reason": "越区",
                            "allowed_districts": denied,
                        },
                    ).status_code
                    == 403
                )
            assert (
                reg.patch(
                    f"/api/v1/accounts/{globalid}",
                    headers=h,
                    json={
                        "version": 1,
                        "role_profile_id": roleid,
                        "enabled": True,
                        "reason": "修改全域",
                    },
                ).status_code
                == 404
            )
            assert (
                reg.post(
                    "/api/v1/roles",
                    headers=h,
                    json={
                        "name": "blocked-" + key,
                        "base_role": "regulator",
                        "permissions": ["stations.read"],
                    },
                ).status_code
                == 403
            )
            empty = reg.post(
                "/api/v1/accounts",
                headers=h,
                json={**body, "username": "region-empty-" + key, "allowed_districts": []},
            )
            assert empty.status_code == 201
            created.append(empty.json()["id"])
            assert reg.get("/api/v1/auth/me").json()["allowed_districts"] == ["范围甲"]
    finally:
        with SessionLocal.begin() as db:
            db.execute(
                delete(Audit).where(Audit.user_id == issuerid, Audit.action.like("account.%"))
            )
            db.execute(delete(LoginSession).where(LoginSession.user_id.in_(created)))
            db.execute(delete(User).where(User.id.in_(created)))
            if roleid:
                db.execute(delete(RoleProfile).where(RoleProfile.id == roleid))
