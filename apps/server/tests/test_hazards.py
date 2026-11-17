import csv
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
import test_inspection_contracts as inspection_cases
from fastapi import HTTPException
from sqlalchemy import delete, select

from app import hazards
from app.db import SessionLocal
from app.models import (
    Audit,
    HazardAction,
    HazardAttachment,
    HazardCase,
    RoleProfile,
    User,
    now,
)

inspection_case = inspection_cases.case


@pytest.fixture()
def case(inspection_case):
    c = inspection_case
    c["hazard_body"] = {
        "station_id": c["station"],
        "operator_id": c["task"]["operator_id"],
        "title": "Hazard " + c["task"]["title"],
        "description": "Synthetic hazard requirement",
        "severity": "major",
        "due_at": (now() + timedelta(days=2)).isoformat(),
    }
    try:
        yield c
    finally:
        with SessionLocal.begin() as db:
            ids = select(HazardCase.id).where(HazardCase.station_id == c["station"])
            db.execute(
                delete(Audit).where(Audit.action.like("hazards.%"), Audit.object_id.in_(ids))
            )
            db.execute(delete(HazardAttachment).where(HazardAttachment.case_id.in_(ids)))
            db.execute(delete(HazardAction).where(HazardAction.case_id.in_(ids)))
            db.execute(delete(HazardCase).where(HazardCase.station_id == c["station"]))


def create(c, **overrides):
    response = c["clients"][0].post(
        "/api/v1/hazards", headers=c["headers"][0], json={**c["hazard_body"], **overrides}
    )
    assert response.status_code == 201, response.text
    return response.json()


def action(c, item, verb, *, actor=None, version=None, **fields):
    actor = (0 if verb == "close" else 1) if actor is None else actor
    return c["clients"][actor].post(
        f"/api/v1/hazards/{item['id']}/actions",
        headers=c["headers"][actor],
        json={
            "action": verb,
            "version": version or item["version"],
            "comment": "Synthetic action",
            **fields,
        },
    )


def proof(c, item):
    result = c["clients"][1].post(
        f"/api/v1/hazards/{item['id']}/attachments",
        headers=c["headers"][1],
        files={"file": ("proof.pdf", b"%PDF-1.4 Synthetic hazard", "application/pdf")},
    )
    assert result.status_code == 201
    return result.json()


def facts():
    return {
        "contact": {"name": "Executor", "phone": "13800000001"},
        "performed_at": (now() - timedelta(hours=1)).isoformat(),
    }


def test_publish_major_does_not_reclassify_inspections_and_close_needs_no_feedback(case):
    c = case
    item = create(c)
    assert (
        item["severity"] == "major"
        and item["status"] == "tracking"
        and item["progress"] == "not_started"
    )
    assert item["closed_at"] is None and item["history"][0]["action"] == "publish"
    assert c["clients"][0].get("/api/v1/hazards", params={"q": item["title"]}).json()["total"] == 1
    inspection = c["clients"][0].get(f"/api/v1/inspections/{c['task']['id']}").json()
    assert inspection["status"] == "assigned" and len(inspection["history"]) == 1
    closed = action(c, item, "close")
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed" and closed.json()["progress"] == "not_started"
    assert closed.json()["closed_at"] is not None and not closed.json()["overdue"]
    assert action(c, closed.json(), "reply", **facts()).status_code == 409
    assert (
        c["clients"][1]
        .post(
            f"/api/v1/hazards/{item['id']}/attachments",
            headers=c["headers"][1],
            files={"file": ("proof.pdf", b"%PDF-1.4", "application/pdf")},
        )
        .status_code
        == 409
    )


def test_start_complete_remains_tracking_frozen_proof_and_explicit_time(case):
    c = case
    item = create(c)
    started = action(c, item, "start")
    assert started.status_code == 200 and started.json()["progress"] == "in_progress"
    first = proof(c, item)
    payload = facts()
    completed = action(c, started.json(), "reply", **payload)
    assert completed.status_code == 200
    item = completed.json()
    assert (
        item["status"] == "tracking"
        and item["progress"] == "completed"
        and item["closed_at"] is None
    )
    assert item["history"][-1]["contact"] == payload["contact"]
    assert datetime.fromisoformat(item["history"][-1]["performed_at"]) == datetime.fromisoformat(
        payload["performed_at"]
    )
    bound = item["materials"][0]
    assert bound["id"] == first["id"] and bound["action_id"] == item["history"][-1]["id"]
    path = f"/api/v1/hazards/{item['id']}/attachments/{first['id']}"
    assert c["clients"][1].delete(path, headers=c["headers"][1]).status_code == 409
    download = c["clients"][1].get(path + "/download")
    assert (
        download.content == b"%PDF-1.4 Synthetic hazard"
        and download.headers["cache-control"] == "no-store"
    )
    assert c["clients"][2].get(path + "/download").status_code == 404
    assert c["clients"][3].get(path + "/download").status_code == 403
    assert action(c, item, "reply", **facts()).status_code == 422
    proof(c, item)
    repeated = action(c, item, "reply", **facts())
    assert repeated.status_code == 200 and repeated.json()["status"] == "tracking"
    closed = action(c, repeated.json(), "close")
    assert closed.status_code == 200 and closed.json()["progress"] == "completed"


@pytest.mark.parametrize(
    "missing", ["contact", "performed_at", "material", "all", "future", "naive", "image"]
)
def test_new_feedback_rejects_missing_facts_and_preserves_all_state(case, missing):
    c = case
    item = create(c)
    fields = facts()
    if missing in {"contact", "performed_at"}:
        fields.pop(missing)
    if missing == "all":
        fields = {}
    if missing == "future":
        fields["performed_at"] = (now() + timedelta(days=1)).isoformat()
    if missing == "naive":
        fields["performed_at"] = now().replace(tzinfo=None).isoformat()
    if missing != "material":
        proof(c, item)
    if missing == "image":
        fields["rich_document"] = inspection_cases.document(image=2147483647)
    before = c["clients"][0].get(f"/api/v1/hazards/{item['id']}").json()
    with SessionLocal() as db:
        old = list(
            db.scalars(
                select(Audit.id).where(
                    Audit.action == "hazards.reply", Audit.object_id == item["id"]
                )
            )
        )
    assert action(c, item, "reply", **fields).status_code == 422
    assert c["clients"][0].get(f"/api/v1/hazards/{item['id']}").json() == before
    with SessionLocal() as db:
        assert (
            list(
                db.scalars(
                    select(Audit.id).where(
                        Audit.action == "hazards.reply", Audit.object_id == item["id"]
                    )
                )
            )
            == old
        )


def test_scope_explicit_target_disabled_and_invalid_deadline(case, accounts):
    c = case
    item = create(c)
    assert c["clients"][2].get(f"/api/v1/hazards/{item['id']}").status_code == 404
    assert c["clients"][2].get("/api/v1/hazards", params={"q": item["title"]}).json()["total"] == 0
    assert c["clients"][3].get("/api/v1/hazards").status_code == 403
    assert action(c, item, "close", actor=1).status_code == 403
    assert action(c, item, "start", actor=0).status_code == 403
    with SessionLocal() as db:
        other = db.scalar(select(User).where(User.username == accounts[0][1])).operator_id
    for changed in [
        {"operator_id": other},
        {"due_at": now().replace(tzinfo=None).isoformat()},
        {"due_at": (now() - timedelta(days=1)).isoformat()},
        {"title": " "},
    ]:
        assert (
            c["clients"][0]
            .post("/api/v1/hazards", headers=c["headers"][0], json={**c["hazard_body"], **changed})
            .status_code
            == 422
        )
    candidates = c["clients"][0].get("/api/v1/hazards/stations", params={"q": c["task"]["title"]})
    assert candidates.status_code == 200


def test_overdue_clock_completion_and_closure_are_separate(case):
    c = case
    item = create(c)
    with SessionLocal.begin() as db:
        db.get(HazardCase, item["id"]).due_at = now() - timedelta(days=1)
    path = f"/api/v1/hazards/{item['id']}"
    assert c["clients"][1].get(path).json()["overdue"]
    proof(c, item)
    done = action(c, item, "reply", **facts()).json()
    assert done["progress"] == "completed" and done["status"] == "tracking" and not done["overdue"]
    assert (
        c["clients"][0]
        .get("/api/v1/hazards", params={"record_id": item["id"], "overdue": True})
        .json()["total"]
        == 0
    )


def test_real_concurrent_start_only_one_transition(case, accounts):
    c = case
    item = create(c)

    def start():
        try:
            with SessionLocal.begin() as db:
                user = db.scalar(select(User).where(User.username == accounts[0][0]))
                hazards.act(
                    db,
                    user,
                    item["id"],
                    hazards.HazardActionInput(version=1, action="start", comment="Start"),
                )
            return 200
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: start(), range(2))) == [200, 409]
    result = c["clients"][0].get(f"/api/v1/hazards/{item['id']}").json()
    assert result["version"] == 2 and len(result["history"]) == 2


def test_projection_failure_does_not_publish_or_close(case, monkeypatch):
    c = case
    item = create(c)
    with monkeypatch.context() as patch:
        patch.setattr(hazards.HazardDetail, "model_validate", inspection_cases.fail)
        assert (
            c["clients"][0]
            .post("/api/v1/hazards", headers=c["headers"][0], json=c["hazard_body"])
            .status_code
            == 500
        )
        assert action(c, item, "close").status_code == 500
    assert c["clients"][0].get("/api/v1/hazards", params={"q": item["title"]}).json()["total"] == 1
    assert c["clients"][0].get(f"/api/v1/hazards/{item['id']}").json() == item


def test_full_query_export_beijing_days_formula_safety_and_overflow(case, monkeypatch):
    from app.routers import hazards as routes

    c = case
    c["clients"][0]._transport.raise_server_exceptions = True
    prefix = c["hazard_body"]["title"]
    items = [create(c, title="=SUM(1,1) " + prefix) for _ in range(3)]
    with SessionLocal.begin() as db:
        for item, stamp in zip(
            items,
            [
                datetime(2026, 11, 14, 16, tzinfo=UTC),
                datetime(2026, 11, 15, 15, 59, tzinfo=UTC),
                datetime(2026, 11, 15, 16, tzinfo=UTC),
            ],
        ):
            db.get(HazardCase, item["id"]).created_at = stamp
    params = {
        "q": prefix,
        "created_from": "2026-11-15",
        "created_to": "2026-11-15",
        "status": "tracking",
        "progress": "not_started",
    }
    page = c["clients"][0].get("/api/v1/hazards", params={**params, "page_size": 1}).json()
    assert page["total"] == 2 and len(page["items"]) == 1
    response = c["clients"][0].get("/api/v1/hazards/export", params=params)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert len(rows) == 2 and all(row["隐患名称"].startswith("'=SUM") for row in rows)
    assert rows[0]["创建时间（北京时间）"] == "2026-11-15T00:00:00+08:00"
    assert c["clients"][2].get("/api/v1/hazards/export", params=params).status_code == 200
    assert (
        len(
            list(
                csv.DictReader(
                    io.StringIO(
                        c["clients"][2]
                        .get("/api/v1/hazards/export", params=params)
                        .content.decode("utf-8-sig")
                    )
                )
            )
        )
        == 0
    )
    assert c["clients"][3].get("/api/v1/hazards/export").status_code == 403
    with SessionLocal() as db:
        before = list(db.scalars(select(Audit.id).where(Audit.action == "hazards.export")))
    monkeypatch.setattr(routes, "EXPORT_LIMIT", 1)
    assert c["clients"][0].get("/api/v1/hazards/export", params=params).status_code == 422
    with SessionLocal() as db:
        assert list(db.scalars(select(Audit.id).where(Audit.action == "hazards.export"))) == before
    for invalid in [
        {"created_from": "2026-11-16", "created_to": "2026-11-15"},
        {"created_to": "9999-12-31"},
    ]:
        assert c["clients"][0].get("/api/v1/hazards", params=invalid).status_code == 422


def test_district_and_independent_grants_never_expand_existing_profiles(case):
    c = case
    item = create(c)
    path = f"/api/v1/hazards/{item['id']}"
    with SessionLocal.begin() as db:
        user = db.get(User, db.get(HazardCase, item["id"]).created_by)
        uid = user.id
        original = user.allowed_districts
        user.allowed_districts = ["no-hazard-district"]
    try:
        assert c["clients"][0].get(path).status_code == 404
        assert (
            c["clients"][0].get("/api/v1/hazards", params={"record_id": item["id"]}).json()["total"]
            == 0
        )
        assert c["clients"][0].get("/api/v1/hazards/stations").json()["total"] == 0
    finally:
        with SessionLocal.begin() as db:
            db.get(User, uid).allowed_districts = original
    with SessionLocal.begin() as db:
        user = db.get(User, uid)
        previous = user.role_profile_id
        profile = RoleProfile(
            name="hazard-profile-" + str(item["id"]),
            base_role="regulator",
            permissions=["stations.read", "hazards.read"],
        )
        db.add(profile)
        db.flush()
        profile_id = profile.id
        user.role_profile_id = profile_id
    try:
        assert c["clients"][0].get(path).status_code == 200
        assert action(c, item, "close").status_code == 403
        assert c["clients"][0].get("/api/v1/hazards/export").status_code == 403
        assert c["clients"][0].get("/api/v1/hazards/stations").status_code == 403
        with SessionLocal.begin() as db:
            db.get(RoleProfile, profile_id).permissions = [
                "stations.read",
                "inspections.read",
                "inspections.close",
            ]
        assert c["clients"][0].get(path).status_code == 403
    finally:
        with SessionLocal.begin() as db:
            db.get(User, uid).role_profile_id = previous
            db.flush()
            db.delete(db.get(RoleProfile, profile_id))
