import secrets
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.config import settings
from app.contracts import InspectionTaskResponse
from app.db import SessionLocal
from app.main import app
from app.models import Audit, InspectionAction, InspectionAttachment, InspectionTask, User, now


@pytest.fixture()
def case(accounts, staff, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    clients = [
        TestClient(
            app,
            client=("inspection-contract-" + secrets.token_hex(6), 1),
            raise_server_exceptions=False,
        )
        for _ in range(4)
    ]
    names = [staff[0][0], accounts[0][0], accounts[0][1], staff[0][1]]
    headers = []
    for client, name in zip(clients, names):
        auth = client.post("/api/v1/auth/login", json={"username": name, "password": accounts[1]})
        assert auth.status_code == 200
        headers.append({"x-csrf-token": auth.json()["csrf_token"]})
    body = {
        "station_id": accounts[2][0],
        "title": "contract-" + secrets.token_hex(8),
        "description": "Synthetic inspection",
        "severity": "major",
        "due_at": (now() + timedelta(days=1)).isoformat(),
    }
    response = clients[0].post("/api/v1/inspections", headers=headers[0], json=body)
    assert response.status_code == 201, response.text
    try:
        yield {
            "clients": clients,
            "headers": headers,
            "body": body,
            "task": response.json(),
            "station": accounts[2][0],
        }
    finally:
        for client in clients:
            client.close()
        with SessionLocal.begin() as db:
            ids = select(InspectionTask.id).where(InspectionTask.station_id.in_(accounts[2]))
            db.execute(
                delete(Audit).where(Audit.action.like("inspections.%"), Audit.object_id.in_(ids))
            )
            db.execute(delete(InspectionAttachment).where(InspectionAttachment.task_id.in_(ids)))
            db.execute(delete(InspectionAction).where(InspectionAction.task_id.in_(ids)))
            db.execute(delete(InspectionTask).where(InspectionTask.station_id.in_(accounts[2])))


def execution_fields():
    return {
        "contact": {"name": "Synthetic executor", "phone": "13800000001"},
        "performed_at": (now() - timedelta(days=1)).isoformat(),
    }


def upload_proof(c):
    response = c["clients"][1].post(
        f"/api/v1/inspections/{c['task']['id']}/attachments",
        headers=c["headers"][1],
        files={"file": ("proof.pdf", b"%PDF-1.4 Synthetic", "application/pdf")},
    )
    assert response.status_code == 201
    return response.json()


def fail(value):
    raise RuntimeError("Projection failed before commit")


def test_create_projection_failure_does_not_persist_task_history_or_audit(case, monkeypatch):
    c = case
    with SessionLocal() as db:
        before = db.scalar(
            select(func.count())
            .select_from(Audit)
            .where(
                Audit.action == "inspections.assign",
                Audit.user_id == db.get(InspectionTask, c["task"]["id"]).created_by,
            )
        )
    title = "failed-" + secrets.token_hex(12)
    with monkeypatch.context() as patch:
        patch.setattr(InspectionTaskResponse, "model_validate", fail)
        result = c["clients"][0].post(
            "/api/v1/inspections", headers=c["headers"][0], json={**c["body"], "title": title}
        )
        assert result.status_code == 500
    with SessionLocal() as db:
        assert db.scalar(select(InspectionTask.id).where(InspectionTask.title == title)) is None
        assert (
            db.scalar(
                select(func.count())
                .select_from(Audit)
                .where(
                    Audit.action == "inspections.assign",
                    Audit.user_id == db.get(InspectionTask, c["task"]["id"]).created_by,
                )
            )
            == before
        )


@pytest.mark.parametrize("action", ["reply", "return", "close"])
def test_action_projection_failure_keeps_status_evidence_and_history(case, monkeypatch, action):
    c = case
    tid = c["task"]["id"]
    owner = c["clients"][1]
    reg = c["clients"][0]
    file = owner.post(
        f"/api/v1/inspections/{tid}/attachments",
        headers=c["headers"][1],
        files={"file": ("proof.pdf", b"%PDF-1.4\nSynthetic", "application/pdf")},
    )
    assert file.status_code == 201, file.text
    if action != "reply":
        assert (
            owner.post(
                f"/api/v1/inspections/{tid}/actions",
                headers=c["headers"][1],
                json={
                    "action": "reply",
                    "version": 1,
                    "comment": "Synthetic reply",
                    **execution_fields(),
                },
            ).status_code
            == 200
        )
    before = reg.get(f"/api/v1/inspections/{tid}").json()
    with SessionLocal() as db:
        bound = db.get(InspectionAttachment, file.json()["id"]).action_id
    actor = 1 if action == "reply" else 0
    with monkeypatch.context() as patch:
        patch.setattr(InspectionTaskResponse, "model_validate", fail)
        response = c["clients"][actor].post(
            f"/api/v1/inspections/{tid}/actions",
            headers=c["headers"][actor],
            json={
                "action": action,
                "version": before["version"],
                "comment": "Failed projection",
                **(execution_fields() if action == "reply" else {}),
            },
        )
        assert response.status_code == 500, response.text
    after = reg.get(f"/api/v1/inspections/{tid}").json()
    assert after == before
    with SessionLocal() as db:
        assert db.get(InspectionAttachment, file.json()["id"]).action_id == bound
        assert (
            db.scalar(
                select(Audit.id).where(
                    Audit.action == "inspections." + action,
                    Audit.object_id == tid,
                    Audit.detail["after"]["version"].as_integer() == before["version"] + 1,
                )
            )
            is None
        )


def test_typed_detail_and_material_reads_preserve_privacy_and_access(case):
    c = case
    tid = c["task"]["id"]
    assert c["clients"][2].get(f"/api/v1/inspections/{tid}").status_code == 404
    assert c["clients"][3].get(f"/api/v1/inspections/{tid}").status_code == 403
    for client in c["clients"][:2]:
        for path in [
            f"/api/v1/inspections/{tid}",
            f"/api/v1/inspections?record_id={tid}",
            f"/api/v1/inspections/{tid}/attachments",
        ]:
            response = client.get(path)
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "no-store"
            assert "storage_key" not in response.text
        detail = client.get(f"/api/v1/inspections/{tid}").json()
        assert detail["history"][0]["action"] == "assign"
        assert detail["operator_id"] == c["task"]["operator_id"]


def test_locked_access_refreshes_cached_version_before_transition(case, accounts):
    from app import inspection

    c = case
    tid = c["task"]["id"]
    upload_proof(c)
    with SessionLocal.begin() as stale:
        cached = stale.get(InspectionTask, tid)
        owner = stale.scalar(select(User).where(User.username == accounts[0][0]))
        response = c["clients"][1].post(
            f"/api/v1/inspections/{tid}/actions",
            headers=c["headers"][1],
            json={
                "action": "reply",
                "version": 1,
                "comment": "Other transaction",
                **execution_fields(),
            },
        )
        assert response.status_code == 200
        assert cached.version == 1
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as error:
            inspection.act(stale, owner, tid, 1, "reply", "Must not overwrite")
        assert error.value.status_code == 409
        assert cached.version == 2 and cached.status == "replied"


def document(text="Formatted requirement", image=None):
    content = [
        {
            "type": "paragraph",
            "content": [{"type": "text", "text": text, "marks": [{"type": "bold"}]}],
        }
    ]
    if image is not None:
        content.append(
            {"type": "privateImage", "attrs": {"attachment_id": image, "alt": "inspection proof"}}
        )
    return {"type": "doc", "content": content}


def test_structured_requirement_is_authoritative_and_legacy_execution_facts_stay_unknown(case):
    c = case
    c["clients"][0]._transport.raise_server_exceptions = True
    response = c["clients"][0].post(
        "/api/v1/inspections",
        headers=c["headers"][0],
        json={**c["body"], "rich_document": document()},
    )
    assert response.status_code == 201
    item = response.json()
    assert item["description"] == "Formatted requirement"
    detail = c["clients"][1].get(f"/api/v1/inspections/{item['id']}").json()
    assert detail["rich_document"]["content"][0]["content"][0]["marks"][0]["type"] == "bold"
    assert detail["history"][0]["rich_document"] == detail["rich_document"]
    assert detail["history"][0]["contact"] is None and detail["history"][0]["performed_at"] is None
    denied = c["clients"][0].post(
        "/api/v1/inspections",
        headers=c["headers"][0],
        json={**c["body"], "rich_document": document(image=1)},
    )
    assert denied.status_code == 422


def test_structured_reply_freezes_pending_image_contact_and_user_time_separately_from_review(case):
    import base64

    c = case
    path = f"/api/v1/inspections/{c['task']['id']}"
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
    )
    file = c["clients"][1].post(
        path + "/attachments",
        headers=c["headers"][1],
        files={"file": ("execution.png", png, "image/png")},
    )
    assert file.status_code == 201
    fid = file.json()["id"]
    stamp = (now() - timedelta(days=2)).isoformat()
    body = {
        "version": 1,
        "action": "reply",
        "comment": "ignored fallback",
        "rich_document": document("Executed safely", fid),
        "contact": {"name": " Manual executor ", "phone": "13800000001"},
        "performed_at": stamp,
    }
    response = c["clients"][1].post(path + "/actions", headers=c["headers"][1], json=body)
    assert response.status_code == 200
    detail = c["clients"][0].get(path).json()
    proof = detail["history"][-1]
    assert proof["comment"].startswith("Executed safely")
    assert proof["contact"] == {"name": "Manual executor", "phone": "13800000001"}
    from datetime import datetime

    assert datetime.fromisoformat(proof["performed_at"]) == datetime.fromisoformat(stamp)
    assert datetime.fromisoformat(proof["performed_at"]) < datetime.fromisoformat(
        proof["created_at"]
    )
    assert c["clients"][1].get(path + "/attachments").json()["items"][0]["action_id"] == proof["id"]
    assert c["clients"][2].get(path).status_code == 404
    assert c["clients"][3].get(path).status_code == 403
    assert (
        c["clients"][0]
        .post(
            path + "/actions",
            headers=c["headers"][0],
            json={"version": 2, "action": "return", "comment": "supplement"},
        )
        .status_code
        == 200
    )
    assert (
        c["clients"][1]
        .post(path + "/actions", headers=c["headers"][1], json={**body, "version": 3})
        .status_code
        == 422
    )
    assert c["clients"][0].get(path).json()["history"][1] == proof
    assert c["clients"][1].get(path + f"/attachments/{fid}/download").content == png


@pytest.mark.parametrize(
    "fields",
    [
        {"contact": {"name": "only name"}},
        {"performed_at": "2026-01-01T00:00:00"},
        {"performed_at": (now() + timedelta(days=1)).isoformat()},
        {"rich_document": document(image=9223372036854770000)},
    ],
)
def test_invalid_execution_facts_do_not_advance_task_or_create_history(case, fields):
    c = case
    path = f"/api/v1/inspections/{c['task']['id']}"
    uploaded = c["clients"][1].post(
        path + "/attachments",
        headers=c["headers"][1],
        files={"file": ("proof.pdf", b"%PDF-1.4 proof", "application/pdf")},
    )
    assert uploaded.status_code == 201
    before = c["clients"][0].get(path).json()
    result = c["clients"][1].post(
        path + "/actions",
        headers=c["headers"][1],
        json={
            "action": "reply",
            "version": 1,
            "comment": "reply",
            "contact": {"name": "Executor", "phone": "13800000001"},
            "performed_at": (now() - timedelta(days=1)).isoformat(),
            **fields,
        },
    )
    assert result.status_code == 422
    assert c["clients"][0].get(path).json() == before


def test_structured_execution_response_failure_rolls_back_all_new_facts(case, monkeypatch):
    c = case
    path = f"/api/v1/inspections/{c['task']['id']}"
    uploaded = c["clients"][1].post(
        path + "/attachments",
        headers=c["headers"][1],
        files={"file": ("proof.pdf", b"%PDF-1.4 rollback", "application/pdf")},
    )
    assert uploaded.status_code == 201
    body = {
        "action": "reply",
        "version": 1,
        "comment": "reply",
        "rich_document": document("Rollback proof"),
        "contact": {"name": "Executor", "phone": "13800000001"},
        "performed_at": (now() - timedelta(days=1)).isoformat(),
    }
    before = c["clients"][0].get(path).json()
    with monkeypatch.context() as patch:
        patch.setattr(InspectionTaskResponse, "model_validate", fail)
        assert (
            c["clients"][1].post(path + "/actions", headers=c["headers"][1], json=body).status_code
            == 500
        )
    assert c["clients"][0].get(path).json() == before
    assert (
        c["clients"][1].post(path + "/actions", headers=c["headers"][1], json=body).status_code
        == 200
    )
    result = c["clients"][0].post(
        path + "/actions",
        headers=c["headers"][0],
        json={
            "action": "close",
            "version": 2,
            "comment": "review",
            "contact": {"name": "Not executor", "phone": "13800000002"},
        },
    )
    assert result.status_code == 422
    assert c["clients"][0].get(path).json()["status"] == "replied"


@pytest.mark.parametrize("rich", [False, True])
@pytest.mark.parametrize(
    "missing", ["contact", "performed_at", "material", "blank_contact", "all_facts"]
)
def test_every_reply_requires_prototype_execution_facts_and_current_material(case, missing, rich):
    c = case
    path = f"/api/v1/inspections/{c['task']['id']}"
    body = {
        "action": "reply",
        "version": 1,
        "comment": "reply",
        "rich_document": document("Structured reply"),
        "contact": {"name": "Executor", "phone": "13800000001"},
        "performed_at": (now() - timedelta(days=1)).isoformat(),
    }
    if not rich:
        body.pop("rich_document")
    if missing == "all_facts":
        body.pop("contact")
        body.pop("performed_at")
    if missing in {"contact", "performed_at"}:
        body.pop(missing)
    if missing == "blank_contact":
        body["contact"] = {"name": " ", "phone": " "}
    if missing != "material":
        response = c["clients"][1].post(
            path + "/attachments",
            headers=c["headers"][1],
            files={"file": ("proof.pdf", b"%PDF-1.4 proof", "application/pdf")},
        )
        assert response.status_code == 201
    before = c["clients"][0].get(path).json()
    materials_before = c["clients"][0].get(path + "/attachments").json()
    with SessionLocal() as db:
        audit_before = list(
            db.scalars(
                select(Audit.id)
                .where(Audit.object_id == c["task"]["id"], Audit.action == "inspections.reply")
                .order_by(Audit.id)
            )
        )
    assert (
        c["clients"][1].post(path + "/actions", headers=c["headers"][1], json=body).status_code
        == 422
    )
    assert c["clients"][0].get(path).json() == before

    assert c["clients"][0].get(path + "/attachments").json() == materials_before
    with SessionLocal() as db:
        assert (
            list(
                db.scalars(
                    select(Audit.id)
                    .where(Audit.object_id == c["task"]["id"], Audit.action == "inspections.reply")
                    .order_by(Audit.id)
                )
            )
            == audit_before
        )


def test_complete_plain_reply_freezes_explicit_facts_without_requiring_rich_formatting(case):
    c = case
    material = upload_proof(c)
    path = f"/api/v1/inspections/{c['task']['id']}"
    facts = execution_fields()
    response = c["clients"][1].post(
        path + "/actions",
        headers=c["headers"][1],
        json={"action": "reply", "version": 1, "comment": "Plain complete feedback", **facts},
    )
    assert response.status_code == 200
    submitted = c["clients"][1].get(path).json()["history"][-1]
    assert submitted["rich_document"] is None
    assert submitted["contact"] == facts["contact"]
    assert datetime.fromisoformat(submitted["performed_at"]) == datetime.fromisoformat(
        facts["performed_at"]
    )
    materials = c["clients"][1].get(path + "/attachments").json()["items"]
    assert materials[0]["id"] == material["id"] and materials[0]["action_id"] == submitted["id"]
    assert (
        c["clients"][1].get(path + f"/attachments/{material['id']}/download").content
        == b"%PDF-1.4 Synthetic"
    )
