import secrets
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app import workflows
from app.config import settings
from app.contracts import OkResponse
from app.db import SessionLocal
from app.main import app
from app.models import ApprovalAction, Attachment, Audit, Station, StationApplication, User
from app.routers import applications


@pytest.fixture()
def case(accounts, staff, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    clients = [
        TestClient(
            app,
            client=("application-transaction-" + secrets.token_hex(8), 1),
            raise_server_exceptions=False,
        )
        for _ in range(2)
    ]
    headers = []
    try:
        for client, name in zip(clients, [accounts[0][0], staff[0][0]], strict=True):
            response = client.post(
                "/api/v1/auth/login", json={"username": name, "password": accounts[1]}
            )
            assert response.status_code == 200
            headers.append({"x-csrf-token": response.json()["csrf_token"]})
        with SessionLocal() as db:
            uid = db.scalar(select(User.id).where(User.username == accounts[0][0]))
            uids = list(
                db.scalars(select(User.id).where(User.username.in_([accounts[0][0], staff[0][0]])))
            )
        yield {
            "clients": clients,
            "headers": headers,
            "sid": accounts[2][0],
            "uid": uid,
            "storage": tmp_path,
            "uids": uids,
        }
    finally:
        for client in clients:
            client.close()
        # Staff fixtures are finalized before the operator's accounts fixture.
        # Remove this case's review references before its temporary reviewer.
        with SessionLocal.begin() as db:
            db.execute(
                delete(ApprovalAction).where(
                    ApprovalAction.application_id.in_(
                        select(StationApplication.id).where(
                            StationApplication.station_id == accounts[2][0]
                        )
                    )
                )
            )


def create(case, kind="access", payload=None):
    response = case["clients"][0].post(
        "/api/v1/applications",
        headers=case["headers"][0],
        json={"station_id": case["sid"], "kind": kind, "payload": payload or {"note": "Original"}},
    )
    assert response.status_code == 201, response.text
    return response.json()


def submit(case, record):
    response = case["clients"][0].post(
        f"/api/v1/applications/{record['id']}/actions",
        headers=case["headers"][0],
        json={"action": "submit", "version": record["version"]},
    )
    assert response.status_code == 200
    return response.json()


def station_state(db, sid):
    station = db.get(Station, sid)
    return station.status, station.version, station.name, station.address


@pytest.mark.parametrize(
    "stage",
    [
        "create",
        "edit",
        "submit",
        "withdraw",
        "reject",
        "approve",
        "change_approve",
        "decommission_approve",
    ],
)
def test_failed_response_projection_cannot_commit_application_station_history_or_audit(
    case, stage, monkeypatch
):
    owner, regulator = case["clients"]
    kind = (
        "change"
        if stage == "change_approve"
        else "decommission"
        if stage == "decommission_approve"
        else "access"
    )
    if kind != "access":
        with SessionLocal.begin() as db:
            db.get(Station, case["sid"]).status = "operating"
    record = None
    before = None
    proof = b"%PDF-1.4\nApplication transaction evidence"
    if stage != "create":
        record = create(
            case, kind, {"note": "Original", "name": "Changed name", "address": "Changed address"}
        )
        response = owner.post(
            f"/api/v1/applications/{record['id']}/attachments",
            headers=case["headers"][0],
            files={"file": ("proof.pdf", proof, "application/pdf")},
        )
        assert response.status_code == 201
        material = response.json()
        if stage not in {"edit", "submit"}:
            record = submit(case, record)
        path = f"/api/v1/applications/{record['id']}"
        before = owner.get(path).json()
    with SessionLocal() as db:
        before_station = station_state(db, case["sid"])
        before_actions = db.scalar(
            select(func.count())
            .select_from(ApprovalAction)
            .where(
                ApprovalAction.application_id.in_(
                    select(StationApplication.id).where(
                        StationApplication.station_id == case["sid"]
                    )
                )
            )
        )
        before_audits = db.scalar(
            select(func.count())
            .select_from(Audit)
            .where(Audit.user_id.in_(case["uids"]), Audit.action.startswith("applications."))
        )
    if stage == "create":
        client, index, method, path = owner, 0, "POST", "/api/v1/applications"
        body = {"station_id": case["sid"], "kind": kind, "payload": {"note": "Original"}}
    elif stage == "edit":
        client, index, method = owner, 0, "PATCH"
        body = {"version": record["version"], "note": "Updated"}
    else:
        action = "approve" if stage.endswith("approve") else stage
        client, index, method = (
            (regulator, 1, "POST") if action in {"approve", "reject"} else (owner, 0, "POST")
        )
        path += "/actions"
        body = {"version": record["version"], "action": action, "comment": "Verified test decision"}
    original = applications.named_output
    with monkeypatch.context() as patch:
        patch.setattr(
            applications,
            "named_output",
            lambda db, a: {**original(db, a), "version": "invalid-response-version"},
        )
        response = client.request(method, path, headers=case["headers"][index], json=body)
        assert response.status_code == 500
    with SessionLocal() as db:
        assert station_state(db, case["sid"]) == before_station
        assert (
            db.scalar(
                select(func.count())
                .select_from(ApprovalAction)
                .where(
                    ApprovalAction.application_id.in_(
                        select(StationApplication.id).where(
                            StationApplication.station_id == case["sid"]
                        )
                    )
                )
            )
            == before_actions
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(Audit)
                .where(Audit.user_id.in_(case["uids"]), Audit.action.startswith("applications."))
            )
            == before_audits
        )
        if record is None:
            assert (
                db.scalar(
                    select(StationApplication.id).where(
                        StationApplication.station_id == case["sid"]
                    )
                )
                is None
            )
        else:
            attachment = db.get(Attachment, material["id"])
            assert (
                attachment is not None
                and (Path(settings.storage_path) / attachment.storage_key).read_bytes() == proof
            )
    if record is not None:
        assert owner.get(f"/api/v1/applications/{record['id']}").json() == before
        assert owner.get(f"/api/v1/attachments/{material['id']}/download").content == proof
    # The original request/version remains usable after the failed projection.
    retried = client.request(method, path, headers=case["headers"][index], json=body)
    assert retried.status_code == (201 if stage == "create" else 200), retried.text
    if stage == "change_approve":
        with SessionLocal() as db:
            assert db.get(Station, case["sid"]).name == "Changed name"


def test_material_delete_response_failure_keeps_database_and_file(case, monkeypatch):
    record = create(case)
    owner = case["clients"][0]
    proof = b"%PDF-1.4\nKeep file on failed projection"
    uploaded = owner.post(
        f"/api/v1/applications/{record['id']}/attachments",
        headers=case["headers"][0],
        files={"file": ("proof.pdf", proof, "application/pdf")},
    )
    assert uploaded.status_code == 201
    aid = uploaded.json()["id"]

    def fail(*args):
        raise RuntimeError("Response unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(OkResponse, "model_validate", fail)
        assert (
            owner.delete(f"/api/v1/attachments/{aid}", headers=case["headers"][0]).status_code
            == 500
        )
    assert owner.get(f"/api/v1/attachments/{aid}/download").content == proof
    assert owner.delete(f"/api/v1/attachments/{aid}", headers=case["headers"][0]).status_code == 200
    assert owner.get(f"/api/v1/attachments/{aid}/download").status_code == 404
    assert not list(case["storage"].iterdir())


@pytest.mark.parametrize("target", ["application", "create_station", "review_station"])
def test_workflow_row_locks_refresh_previously_cached_versions(case, target):
    record = create(case) if target != "create_station" else None
    if target == "review_station":
        record = submit(case, record)
    with SessionLocal() as cached:
        user = cached.get(User, case["uid"])
        if target == "application":
            retained = cached.get(StationApplication, record["id"])
            assert retained.version == 1
            with SessionLocal.begin() as other:
                workflows.act(other, other.get(User, case["uid"]), record["id"], "submit", 1, "")
            with pytest.raises(HTTPException) as conflict:
                workflows.act(cached, user, record["id"], "submit", 1, "")
        else:
            retained = cached.get(Station, case["sid"])
            assert retained.status == "pending"
            with SessionLocal.begin() as other:
                station = other.get(Station, case["sid"])
                station.status = "operating"
                station.version += 1
            with pytest.raises(HTTPException) as conflict:
                if target == "create_station":
                    workflows.create(cached, user, case["sid"], "access", {})
                else:
                    from types import SimpleNamespace

                    workflows.act(
                        cached,
                        SimpleNamespace(id=user.id, role="regulator"),
                        record["id"],
                        "approve",
                        2,
                        "Verified",
                    )
        assert conflict.value.status_code == 409
        assert retained.version == 2
