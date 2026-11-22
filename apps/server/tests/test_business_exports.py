# ruff: noqa: F811 - pytest resolves imported fixtures by parameter name
import csv
import io
import secrets
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, insert, select
from subsidy_samples import create_claim as sample_claim
from test_rating import rating_fixture  # noqa: F401 - shared pytest fixture
from test_subsidy import finance  # noqa: F401 - shared pytest fixture

from app import rating, rating_results, subsidy
from app.authorization import normalize_grants
from app.db import SessionLocal
from app.main import app
from app.models import Audit, RatingAction, RoleProfile, Station, SubsidyAction, SubsidyClaim, User


@pytest.fixture
def report_staff(staff):
    yield staff
    with SessionLocal.begin() as db:
        ids = select(User.id).where(User.username.in_(staff[0]))
        db.execute(delete(SubsidyAction).where(SubsidyAction.actor_id.in_(ids)))
        db.execute(delete(RatingAction).where(RatingAction.actor_id.in_(ids)))


def login(name, password):
    client = TestClient(app, client=("exports-" + secrets.token_hex(8), 1))
    assert (
        client.post("/api/v1/auth/login", json={"username": name, "password": password}).status_code
        == 200
    )
    return client


def rows(response):
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))


def test_subsidy_export_is_scoped_frozen_and_granted(finance, accounts, report_staff):
    staff = report_staff
    uid, sid, pid = finance
    names, password, _ = accounts
    with SessionLocal.begin() as db:
        owner = db.get(User, uid)
        regulator = db.scalar(select(User).where(User.username == staff[0][0]))
        db.get(Station, sid).name = '=HYPERLINK("https://invalid")'
        c = sample_claim(db, owner, sid, pid, date(2026, 11, 1), date(2026, 11, 2))
        cid = c.id
        subsidy.act(db, owner, cid, "submit", 1, "")
        subsidy.act(db, regulator, cid, "adjust", 2, "=formula", Decimal("-0.02"))
        subsidy.act(db, regulator, cid, "approve", 3, "approved")
        db.add(SubsidyAction(claim_id=cid, actor_id=uid, action="legacy", comment="old", delta=0))
    owner = login(names[0], password)
    other = login(names[1], password)
    association = login(staff[0][1], password)
    path = "/api/v1/subsidy/claims"
    record = rows(owner.get(path + "/export", params={"record_id": cid}))[0]
    assert record["调整后金额(元，非到账证明)"] == "3.00"
    assert record["当前场站名称"].startswith("'=")
    assert rows(other.get(path + "/export", params={"record_id": cid})) == []
    assert association.get(path + "/export").status_code == 403
    history = rows(owner.get(path + "/process/export", params={"record_id": cid}))
    adjustment = next(row for row in history if row["动作"] == "adjust")
    assert adjustment["变更前调整后金额(元)"] == "3.02"
    assert adjustment["变更后调整后金额(元)"] == "3.00"
    assert adjustment["变更前版本"] == "2" and adjustment["变更后版本"] == "3"
    assert adjustment["办理意见"] == "'=formula"
    legacy = next(row for row in history if row["动作"] == "legacy")
    assert legacy["变更后调整后金额(元)"] == "暂无数据"
    assert "旧记录未留存" in legacy["历史快照状态"]
    assert rows(owner.get(path + "/export", params={"q": "%"})) == []
    assert owner.get(path + "/export", params={"status": "invalid"}).status_code == 422
    assert owner.get(path + "/export", params={"kind": "initial"}).status_code == 422
    with SessionLocal.begin() as db:
        user = db.get(User, uid)
        profile = RoleProfile(
            name="export-readonly-" + secrets.token_hex(8),
            base_role="operator",
            permissions=["stations.read", "subsidy.read"],
            version=1,
        )
        db.add(profile)
        db.flush()
        profile_id = profile.id
        user.role_profile_id = profile_id
    try:
        assert owner.get(path + "/export").status_code == 403
        assert owner.get(path + "/process/export").status_code == 403
        with SessionLocal.begin() as db:
            db.get(RoleProfile, profile_id).permissions = ["stations.read", "subsidy.export"]
        assert owner.get(path + "/export").status_code == 403
        assert "subsidy.export" not in normalize_grants(
            "operator", ["subsidy.read", "subsidy.write"]
        )
    finally:
        with SessionLocal.begin() as db:
            db.get(User, uid).role_profile_id = None
            db.execute(delete(RoleProfile).where(RoleProfile.id == profile_id))


def test_rating_export_uses_real_transition_not_parent_or_current_value(
    rating_fixture, accounts, report_staff
):
    staff = report_staff
    uid, sid, standard_id = rating_fixture
    with SessionLocal.begin() as db:
        owner = db.get(User, uid)
        reviewer = db.scalar(select(User).where(User.username == staff[0][1]))
        a = rating.create(db, owner, sid, standard_id, {"service": "80"}, "")
        aid = a.id
        rating.act(db, owner, aid, "submit", 1, "", {})
        rating.act(db, reviewer, aid, "review", 2, "review", {"runtime": "90", "service": "90"})
        rating.act(
            db,
            reviewer,
            aid,
            "publish",
            3,
            "publish",
            {},
            rating_results.local_day() + timedelta(days=30),
        )
        pending = rating.create(
            db,
            owner,
            sid,
            standard_id,
            {"service": "95"},
            "申请复评",
            "reassessment",
            aid,
            4,
        )
        pending_id = pending.id
        rating.act(db, owner, pending_id, "submit", 1, "", {})
        rating.act(db, reviewer, aid, "delist", 4, "delist", {})
    client = login(staff[0][1], accounts[1])
    other = login(accounts[0][1], accounts[1])
    path = "/api/v1/ratings/applications"
    exported = rows(client.get(path + "/export", params={"record_id": aid}))
    assert len(exported) == 1 and exported[0]["当前办理状态"] == "delisted"
    assert exported[0]["评审星级(非当前有效星级)"] == "3"
    assert exported[0]["当前有效星级"] == "0"
    assert exported[0]["当前结果状态"] == "delisted"
    pending_query = {"record_id": pending_id}
    record = client.get(path, params=pending_query).json()["items"][0]
    assert record["status"] == "submitted" and record["effective_stars"] is None
    assert record["current_station_result"]["result_state"] == "delisted"
    assert record["current_station_result"]["effective_stars"] == 0
    assert record["station_code"]
    assert other.get(path, params=pending_query).json()["items"] == []
    pending_export = rows(client.get(path + "/export", params=pending_query))[0]
    assert pending_export["场站当前有效星级"] == "0"
    assert pending_export["来源场站编号"] == record["station_code"]
    history = rows(client.get(path + "/process/export", params={"record_id": aid}))
    review = next(row for row in history if row["动作"] == "review")
    assert review["变更前评审分数"] == "暂无数据" and review["变更后评审分数"] == "90.00"
    assert review["变更前办理状态"] == "submitted" and review["变更后办理状态"] == "reviewed"
    publish = next(row for row in history if row["动作"] == "publish")
    assert publish["变更前有效期至"] == "暂无数据" and publish["变更后有效期至"] != "暂无数据"
    assert publish["变更后办理状态"] == "published"
    assert rows(other.get(path + "/process/export", params={"record_id": aid})) == []
    assert client.get(path + "/export", params={"kind": "invalid"}).status_code == 422
    assert "ratings.export" not in normalize_grants(
        "association", ["ratings.read", "ratings.write"]
    )
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.username == staff[0][1]))
        user.allowed_districts = []
    assert rows(client.get(path + "/export", params={"record_id": aid})) == []
    assert rows(client.get(path + "/process/export", params={"record_id": aid})) == []


def test_export_limit_rejects_without_partial_file_or_success_audit(finance, accounts):
    uid, sid, pid = finance
    with SessionLocal.begin() as db:
        payload = {
            "station_id": sid,
            "operator_id": db.get(User, uid).operator_id,
            "policy_id": pid,
            "start_date": date(2026, 11, 1),
            "end_date": date(2026, 11, 2),
            "total_kwh": "1",
            "base_amount": "0.10",
            "adjusted_amount": "0.10",
            "snapshot": {},
        }
        db.execute(insert(SubsidyClaim), [payload for _ in range(10001)])
        cid = db.scalar(select(SubsidyClaim.id).where(SubsidyClaim.policy_id == pid))
        db.execute(
            insert(SubsidyAction),
            [
                {"claim_id": cid, "actor_id": uid, "action": "legacy", "comment": ""}
                for _ in range(10001)
            ],
        )
        before = db.scalar(select(func.count()).select_from(Audit).where(Audit.user_id == uid))
    client = login(accounts[0][0], accounts[1])
    for path, params in [("export", {}), ("process/export", {"record_id": cid})]:
        response = client.get("/api/v1/subsidy/claims/" + path, params=params)
        assert response.status_code == 422 and "10000" in response.json()["detail"]
        assert "Content-Disposition" not in response.headers
    with SessionLocal() as db:
        assert (
            db.scalar(select(func.count()).select_from(Audit).where(Audit.user_id == uid)) == before
        )
