import secrets
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, func, select

from app import inspection_plans as plans
from app.db import SessionLocal, engine
from app.main import app
from app.models import (
    Audit,
    InspectionAction,
    InspectionAttachment,
    InspectionPlan,
    InspectionTask,
    LoginSession,
    RoleProfile,
    Station,
    User,
    now,
)


@pytest.fixture()
def case(accounts, staff):
    reg_name = staff[0][0]
    second_name = "plan-reg-" + secrets.token_hex(8)
    with SessionLocal.begin() as db:
        first = db.scalar(select(User).where(User.username == reg_name))
        second = User(username=second_name, password_hash=first.password_hash, role="regulator")
        db.add(second)
        db.flush()
        second_id = second.id
        first_id = first.id
    profiles = []
    with ExitStack() as stack:
        clients = []
        headers = []
        for name in [reg_name, *accounts[0], staff[0][1], second_name]:
            client = stack.enter_context(
                TestClient(
                    app, client=("plan-" + secrets.token_hex(6), 1), raise_server_exceptions=False
                )
            )
            response = client.post(
                "/api/v1/auth/login", json={"username": name, "password": accounts[1]}
            )
            assert response.status_code == 200
            clients.append(client)
            headers.append({"x-csrf-token": response.json()["csrf_token"]})
        try:
            yield {
                "clients": clients,
                "headers": headers,
                "stations": accounts[2],
                "reg": first_id,
                "second": second_id,
                "profiles": profiles,
                "company_query": accounts[0][0],
            }
        finally:
            with SessionLocal.begin() as db:
                task_ids = select(InspectionTask.id).where(
                    InspectionTask.station_id.in_(accounts[2])
                )
                db.execute(
                    delete(InspectionAttachment).where(InspectionAttachment.task_id.in_(task_ids))
                )
                db.execute(delete(InspectionAction).where(InspectionAction.task_id.in_(task_ids)))
                db.execute(delete(InspectionTask).where(InspectionTask.station_id.in_(accounts[2])))
                db.execute(
                    delete(InspectionPlan).where(
                        InspectionPlan.created_by.in_([first_id, second_id])
                    )
                )
                db.get(User, first_id).role_profile_id = None
                db.execute(delete(Audit).where(Audit.user_id == second_id))
                db.execute(delete(LoginSession).where(LoginSession.user_id == second_id))
                db.delete(db.get(User, second_id))
                if profiles:
                    db.execute(delete(RoleProfile).where(RoleProfile.id.in_(profiles)))


def create(c, key=None, actor=0):
    return c["clients"][actor].post(
        "/api/v1/inspection-plans",
        headers=c["headers"][actor],
        json={"request_key": str(key or uuid4())},
    )


def body(c, version=1, **changes):
    return {
        "version": version,
        "title": "Plan test",
        "description": "Inspect all targets",
        "rich_document": None,
        "station_ids": c["stations"],
        "due_at": None,
        **changes,
    }


def save(c, identifier, value):
    return c["clients"][0].put(
        f"/api/v1/inspection-plans/{identifier}", headers=c["headers"][0], json=value
    )


def publish(c, identifier, version=2):
    return c["clients"][0].post(
        f"/api/v1/inspection-plans/{identifier}/publish",
        headers=c["headers"][0],
        json={"version": version},
    )


def read(c, identifier):
    return c["clients"][0].get(f"/api/v1/inspection-plans/{identifier}")


def task_count(c):
    with SessionLocal() as db:
        return db.scalar(
            select(func.count())
            .select_from(InspectionTask)
            .where(InspectionTask.station_id.in_(c["stations"]))
        )


def test_partial_draft_is_private_and_creation_replay_never_overwrites_saved_work(case):
    c = case
    key = uuid4()
    first = create(c, key)
    assert first.status_code == 201
    draft = first.json()
    assert draft["status"] == "draft" and draft["targets"] == [] and draft["due_at"] is None
    saved = save(c, draft["id"], body(c, title="", description="", station_ids=[]))
    assert saved.status_code == 200 and saved.json()["version"] == 2
    assert publish(c, draft["id"]).status_code == 422 and task_count(c) == 0
    assert create(c, key).json() == saved.json()
    other = create(c, key, actor=4)
    assert other.status_code == 201 and other.json()["id"] != draft["id"]
    for actor in [1, 2, 3, 4]:
        expected = 404 if actor == 4 else 403
        assert (
            c["clients"][actor].get(f"/api/v1/inspection-plans/{draft['id']}").status_code
            == expected
        )
    for actor in [1, 2, 3]:
        assert c["clients"][actor].get("/api/v1/inspection-plans/stations").status_code == 403
    assert c["clients"][1].get("/api/v1/inspections").json()["total"] == 0


def test_atomic_publication_creates_independent_scoped_tasks_without_fabricated_deadline(case):
    c = case
    identifier = create(c).json()["id"]
    assert save(c, identifier, body(c)).status_code == 200
    assert task_count(c) == 0
    result = publish(c, identifier)
    assert result.status_code == 200
    data = result.json()
    assert (
        data["status"] == "published"
        and data["published_count"] == 2
        and data["published_at"]
        and data["version"] == 3
    )
    assert publish(c, identifier).json() == data and task_count(c) == 2
    assert publish(c, identifier, 1).status_code == 409
    for index, target in enumerate(data["targets"]):
        task = c["clients"][index + 1].get(f"/api/v1/inspections/{target['task_id']}")
        assert task.status_code == 200
        detail = task.json()
        assert (
            detail["plan_id"] == identifier
            and detail["due_at"] is None
            and detail["overdue"] is False
        )
        assert [a["action"] for a in detail["history"]] == ["assign"]
        assert (
            c["clients"][2 - index].get(f"/api/v1/inspections/{target['task_id']}").status_code
            == 404
        )
    query = c["clients"][1].get("/api/v1/inspections?overdue=false").json()
    assert query["total"] == 1 and query["items"][0]["due_at"] is None
    assert c["clients"][1].get("/api/v1/inspections?overdue=true").json()["total"] == 0
    assert save(c, identifier, body(c, version=3)).status_code == 409
    assert (
        c["clients"][0]
        .delete(f"/api/v1/inspection-plans/{identifier}?version=3", headers=c["headers"][0])
        .status_code
        == 409
    )


def test_target_recipient_drift_is_detected_and_can_only_be_confirmed_by_a_new_save(case):
    c = case
    identifier = create(c).json()["id"]
    assert save(c, identifier, body(c, station_ids=[c["stations"][0]])).status_code == 200
    with SessionLocal.begin() as db:
        db.get(Station, c["stations"][0]).operator_id = db.get(
            Station, c["stations"][1]
        ).operator_id
    assert read(c, identifier).json()["targets"][0]["recipient_changed"] is True
    assert publish(c, identifier).status_code == 409 and task_count(c) == 0
    assert (
        save(c, identifier, body(c, version=2, station_ids=[c["stations"][0]])).status_code == 200
    )
    result = publish(c, identifier, 3)
    assert result.status_code == 200
    task_id = result.json()["targets"][0]["task_id"]
    assert c["clients"][1].get(f"/api/v1/inspections/{task_id}").status_code == 404
    assert c["clients"][2].get(f"/api/v1/inspections/{task_id}").status_code == 200


def test_revoked_district_redacts_names_and_blocks_whole_publication_but_allows_target_removal(
    case,
):
    c = case
    identifier = create(c).json()["id"]
    assert save(c, identifier, body(c)).status_code == 200
    with SessionLocal.begin() as db:
        db.get(Station, c["stations"][1]).district = "outside-current-scope"
        db.get(User, c["reg"]).allowed_districts = ["测试"]
    detail = read(c, identifier).json()
    assert detail["targets"][1]["accessible"] is False
    assert (
        detail["targets"][1]["station_name"] is None
        and detail["targets"][1]["operator_name"] is None
    )
    assert detail["targets"][1]["recipient_changed"] is None
    assert publish(c, identifier).status_code == 409 and task_count(c) == 0
    assert save(c, identifier, body(c, version=2)).status_code == 404
    assert read(c, identifier).json()["version"] == 2
    options = (
        c["clients"][0]
        .get("/api/v1/inspection-plans/stations?page_size=1&q=" + c["company_query"])
        .json()
    )
    assert options["total"] == 1 and options["items"][0]["id"] == c["stations"][0]
    assert (
        save(c, identifier, body(c, version=2, station_ids=[c["stations"][0]])).status_code == 200
    )
    assert publish(c, identifier, 3).status_code == 200 and task_count(c) == 1


@pytest.mark.parametrize(
    "grants,allowed",
    [
        (["inspections.read"], False),
        (["inspections.assign"], False),
        (["inspections.read", "inspections.assign"], True),
    ],
)
def test_planning_requires_both_grants_and_does_not_implicitly_need_station_profile_read(
    case, grants, allowed
):
    c = case
    with SessionLocal.begin() as db:
        profile = RoleProfile(
            name="plan-role-" + secrets.token_hex(7), base_role="regulator", permissions=grants
        )
        db.add(profile)
        db.flush()
        c["profiles"].append(profile.id)
        db.get(User, c["reg"]).role_profile_id = profile.id
    expected = 200 if allowed else 403
    assert c["clients"][0].get("/api/v1/inspection-plans/stations").status_code == expected
    result = create(c)
    assert result.status_code == (201 if allowed else 403)
    assert c["clients"][0].get("/api/v1/stations").status_code == 403


@pytest.mark.parametrize("operation", ["create", "save", "publish", "delete"])
def test_response_projection_failure_rolls_back_plan_tasks_actions_and_audit(
    case, monkeypatch, operation
):
    c = case
    draft = create(c).json()
    identifier = draft["id"]
    if operation == "publish":
        assert save(c, identifier, body(c)).status_code == 200
    before = read(c, identifier).json()
    with SessionLocal() as db:
        count = db.scalar(
            select(func.count())
            .select_from(InspectionPlan)
            .where(InspectionPlan.created_by == c["reg"])
        )
        audits = db.scalar(select(func.count()).select_from(Audit).where(Audit.user_id == c["reg"]))

    def fail(value):
        raise RuntimeError("Projection failed before commit")

    with monkeypatch.context() as patch:
        patch.setattr(plans.InspectionPlanDetail, "model_validate", fail)
        if operation == "create":
            result = create(c)
        elif operation == "save":
            result = save(c, identifier, body(c))
        elif operation == "publish":
            result = publish(c, identifier)
        else:
            result = c["clients"][0].delete(
                f"/api/v1/inspection-plans/{identifier}?version=1", headers=c["headers"][0]
            )
        assert result.status_code == 500
    assert read(c, identifier).json() == before and task_count(c) == 0
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(InspectionPlan)
                .where(InspectionPlan.created_by == c["reg"])
            )
            == count
        )
        assert (
            db.scalar(select(func.count()).select_from(Audit).where(Audit.user_id == c["reg"]))
            == audits
        )
    if operation == "publish":
        assert publish(c, identifier).status_code == 200


def test_concurrent_publish_replay_returns_same_receipt_and_no_duplicate_assignments(case):
    c = case
    identifier = create(c).json()["id"]
    assert save(c, identifier, body(c)).status_code == 200
    gate = Barrier(2)

    def run():
        gate.wait(timeout=10)
        return publish(c, identifier)

    with ThreadPoolExecutor(max_workers=2) as executor:
        a, b = list(executor.map(lambda _: run(), range(2)))
    assert a.status_code == b.status_code == 200
    assert a.json() == b.json() and task_count(c) == 2
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(InspectionAction)
                .join(InspectionTask, InspectionTask.id == InspectionAction.task_id)
                .where(InspectionTask.plan_id == identifier)
            )
            == 2
        )


def test_draft_versions_and_deletion_do_not_generate_tasks(case):
    c = case
    identifier = create(c).json()["id"]
    assert save(c, identifier, body(c)).status_code == 200
    assert save(c, identifier, body(c, title="stale change")).status_code == 409
    assert (
        c["clients"][0]
        .delete(f"/api/v1/inspection-plans/{identifier}?version=1", headers=c["headers"][0])
        .status_code
        == 409
    )
    deleted = c["clients"][0].delete(
        f"/api/v1/inspection-plans/{identifier}?version=2", headers=c["headers"][0]
    )
    assert deleted.status_code == 200 and deleted.json()["status"] == "deleted"
    assert publish(c, identifier, 3).status_code == 409 and task_count(c) == 0
    assert c["clients"][0].get("/api/v1/inspection-plans").json()["total"] == 0
    assert c["clients"][0].get("/api/v1/inspection-plans?status=deleted").json()["total"] == 1


def test_invalid_targets_images_and_elapsed_deadlines_leave_draft_unchanged(case):
    c = case
    identifier = create(c).json()["id"]
    for fields in [
        {"station_ids": [c["stations"][0]] * 2},
        {"station_ids": list(range(1, 102))},
        {"station_ids": [-1]},
        {"due_at": "2026-01-01T00:00:00"},
        {
            "description": "Text",
            "rich_document": {
                "type": "doc",
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "text"}]},
                    {"type": "privateImage", "attrs": {"attachment_id": 1, "alt": "not allowed"}},
                ],
            },
        },
    ]:
        assert save(c, identifier, body(c, **fields)).status_code == 422
        assert read(c, identifier).json()["version"] == 1
    assert (
        save(c, identifier, body(c, due_at=(now() - timedelta(days=1)).isoformat())).status_code
        == 200
    )
    assert publish(c, identifier).status_code == 422 and task_count(c) == 0


def test_plan_projection_reads_one_postgres_snapshot_during_concurrent_updates(case):
    c = case
    identifier = create(c).json()["id"]
    assert save(c, identifier, body(c, station_ids=[c["stations"][0]])).status_code == 200
    fired = False

    def update_after_scope(connection, cursor, statement, parameters, context, executemany):
        nonlocal fired
        if fired or "FROM inspection_plans" not in statement:
            return
        fired = True
        with SessionLocal.begin() as db:
            row = db.get(InspectionPlan, identifier)
            row.title = "concurrent new title"
            row.version += 1
            db.get(Station, c["stations"][0]).name = "concurrent new station"

    event.listen(engine, "after_cursor_execute", update_after_scope)
    try:
        first = read(c, identifier).json()
    finally:
        event.remove(engine, "after_cursor_execute", update_after_scope)
    assert (
        fired
        and first["title"] == "Plan test"
        and first["version"] == 2
        and first["targets"][0]["station_name"] == "隔离测试"
    )
    second = read(c, identifier).json()
    assert (
        second["title"] == "concurrent new title"
        and second["version"] == 3
        and second["targets"][0]["station_name"] == "concurrent new station"
    )


def test_concurrent_draft_edits_compare_versions_before_overwriting_targets(case):
    c = case
    identifier = create(c).json()["id"]
    gate = Barrier(2)

    def update(index):
        gate.wait(timeout=10)
        return save(
            c, identifier, body(c, title=f"Editor {index}", station_ids=[c["stations"][index]])
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(update, range(2)))
    assert sorted(result.status_code for result in results) == [200, 409]
    winner = next(result.json() for result in results if result.status_code == 200)
    assert read(c, identifier).json() == winner and winner["version"] == 2 and task_count(c) == 0


def test_publication_receipt_does_not_restore_revoked_station_or_task_access(case):
    c = case
    identifier = create(c).json()["id"]
    assert save(c, identifier, body(c)).status_code == 200
    assert publish(c, identifier).status_code == 200
    original = publish(c, identifier).json()
    first_task = original["targets"][0]["task_id"]
    original_operator = (
        c["clients"][0].get(f"/api/v1/inspections/{first_task}").json()["operator_id"]
    )
    with SessionLocal.begin() as db:
        db.get(Station, c["stations"][0]).operator_id = db.get(
            Station, c["stations"][1]
        ).operator_id
    changed = read(c, identifier).json()
    assert changed["targets"][0]["recipient_changed"] is True
    assert (
        c["clients"][0].get(f"/api/v1/inspections/{first_task}").json()["operator_id"]
        == original_operator
    )
    with SessionLocal.begin() as db:
        db.get(User, c["reg"]).allowed_districts = []
    receipt = publish(c, identifier).json()
    assert receipt["published_count"] == 2 and receipt["status"] == "published"
    assert all(
        not target["accessible"]
        and target["task_id"] is None
        and target["station_name"] is None
        and target["operator_name"] is None
        for target in receipt["targets"]
    )
    assert task_count(c) == 2


def test_batch_rolls_back_if_second_station_assignment_fails(case, monkeypatch):
    from app import inspection

    c = case
    identifier = create(c).json()["id"]
    assert save(c, identifier, body(c)).status_code == 200
    original = inspection.create
    called = 0

    def failing(*args, **kwargs):
        nonlocal called
        called += 1
        if called == 2:
            raise RuntimeError("Synthetic second target failure")
        return original(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(inspection, "create", failing)
        assert publish(c, identifier).status_code == 500
    assert called == 2 and task_count(c) == 0
    assert (
        read(c, identifier).json()["version"] == 2
        and read(c, identifier).json()["status"] == "draft"
    )
    assert publish(c, identifier).status_code == 200 and task_count(c) == 2


def test_creator_private_lists_and_station_candidates_are_bounded_searchable_pages(case):
    c = case
    with SessionLocal.begin() as db:
        for index in range(12):
            db.add(
                InspectionPlan(
                    created_by=c["reg"], request_key=str(uuid4()), title=f"page-test-{index}"
                )
            )
        for index, identifier in enumerate(c["stations"]):
            db.get(Station, identifier).name = (
                "unique-plan-option-" + str(c["reg"]) + "-" + str(index)
            )
    first = c["clients"][0].get("/api/v1/inspection-plans?q=page-test&page_size=10").json()
    second = c["clients"][0].get("/api/v1/inspection-plans?q=page-test&page_size=10&page=2").json()
    assert (
        first["total"] == second["total"] == 12
        and len(first["items"]) == 10
        and len(second["items"]) == 2
    )
    assert not {row["id"] for row in first["items"]}.intersection(
        row["id"] for row in second["items"]
    )
    assert c["clients"][4].get("/api/v1/inspection-plans?q=page-test").json()["total"] == 0
    query = "unique-plan-option-" + str(c["reg"])
    pages = [
        c["clients"][0]
        .get(f"/api/v1/inspection-plans/stations?q={query}&page_size=1&page={page}")
        .json()
        for page in [1, 2]
    ]
    assert all(page["total"] == 2 and len(page["items"]) == 1 for page in pages)
    assert {page["items"][0]["id"] for page in pages} == set(c["stations"])
    assert c["clients"][0].get("/api/v1/inspection-plans?page_size=101").status_code == 422


def test_other_author_cannot_update_delete_or_publish_another_creators_draft(case):
    c = case
    identifier = create(c).json()["id"]
    other = c["clients"][4]
    headers = c["headers"][4]
    assert (
        other.put(
            f"/api/v1/inspection-plans/{identifier}", headers=headers, json=body(c)
        ).status_code
        == 404
    )
    assert (
        other.post(
            f"/api/v1/inspection-plans/{identifier}/publish", headers=headers, json={"version": 1}
        ).status_code
        == 404
    )
    assert (
        other.delete(
            f"/api/v1/inspection-plans/{identifier}?version=1", headers=headers
        ).status_code
        == 404
    )
    assert read(c, identifier).json()["version"] == 1 and task_count(c) == 0
