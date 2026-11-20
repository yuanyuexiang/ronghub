import io
import json
import secrets
import zipfile
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, func, select

from app import application_evidence as evidence
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import (
    ApplicationSubmission,
    Attachment,
    RoleProfile,
    Station,
    StationApplication,
    User,
    now,
)


@pytest.fixture()
def flow(accounts, staff, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    names, password, ids = accounts
    with ExitStack() as stack:
        clients = []
        for name in [*names, *staff[0]]:
            client = stack.enter_context(
                TestClient(app, client=("app-proof-" + secrets.token_hex(6), 1))
            )
            assert (
                client.post(
                    "/api/v1/auth/login", json={"username": name, "password": password}
                ).status_code
                == 200
            )
            clients.append(client)
        owner = clients[0]
        headers = [{"x-csrf-token": c.get("/api/v1/auth/me").json()["csrf_token"]} for c in clients]
        created = owner.post(
            "/api/v1/applications",
            headers=headers[0],
            json={
                "station_id": ids[0],
                "kind": "access",
                "payload": {
                    "note": "initial-note",
                    "contact": {"name": "申请专用联系人", "phone": "APP-CONTACT-ONLY"},
                },
            },
        )
        assert created.status_code == 201
        try:
            yield {
                "clients": clients,
                "headers": headers,
                "aid": created.json()["id"],
                "sid": ids[0],
                "names": names,
                "tmp": tmp_path,
            }
        finally:
            from app.models import ApprovalAction

            with SessionLocal.begin() as db:
                db.execute(
                    delete(ApprovalAction).where(
                        ApprovalAction.application_id == created.json()["id"]
                    )
                )


def request_context(f):
    return f["clients"][0].get(f"/api/v1/applications/{f['aid']}/review-context").json()


def action(f, verb, client=0, token=None):
    c = f["clients"][client]
    current = c.get(f"/api/v1/applications/{f['aid']}").json()
    return c.post(
        f"/api/v1/applications/{f['aid']}/actions",
        headers=f["headers"][client],
        json={
            "action": verb,
            "version": current["version"],
            "comment": "proof workflow",
            "review_token": token,
        },
    )


def upload(f, name="proof.pdf", data=b"%PDF-1.4 proof"):
    r = f["clients"][0].post(
        f"/api/v1/applications/{f['aid']}/attachments",
        headers=f["headers"][0],
        files={"file": (name, data)},
    )
    assert r.status_code == 201
    return r.json()


def versions(f):
    return f["clients"][0].get(f"/api/v1/applications/{f['aid']}/submissions").json()["items"]


def snapshot(f, sid, client=0):
    return f["clients"][client].get(f"/api/v1/applications/{f['aid']}/submissions/{sid}")


def test_public_source_projection_contact_privacy_and_review_compare_and_swap(flow):
    f = flow
    c = f["clients"][0]
    with SessionLocal.begin() as db:
        s = db.get(Station, f["sid"])
        s.station_contact_name = "SOURCE-PRIVATE-NAME"
        s.station_contact_phone = "SOURCE-PRIVATE-PHONE"
        s.supply_type = "direct"
    value = request_context(f)
    assert "SOURCE-PRIVATE" not in json.dumps(value)
    assert value["content"]["payload"]["contact"]["phone"] == "APP-CONTACT-ONLY"
    assert value["content"]["source_profile"]["facilities"]["supply_type"] == "direct"
    token = value["review_token"]
    with SessionLocal.begin() as db:
        db.get(Station, f["sid"]).name = "changed-source"
    assert action(f, "submit", token=token).status_code == 409
    assert (
        versions(f) == [] and c.get(f"/api/v1/applications/{f['aid']}").json()["status"] == "draft"
    )
    fresh = request_context(f)
    with SessionLocal.begin() as db:
        db.get(Station, f["sid"]).synced_at = now() + timedelta(seconds=1)
    assert request_context(f)["review_token"] == fresh["review_token"]
    assert action(f, "submit", token=fresh["review_token"]).status_code == 200
    proof = snapshot(f, versions(f)[0]["id"]).json()
    assert (
        proof["source_reviewed"] is True
        and proof["content"]["source_profile"]["station_name"] == "changed-source"
    )
    assert "SOURCE-PRIVATE" not in json.dumps(proof)


def test_submission_roundtrips_preserve_old_source_payload_category_and_retained_file(flow):
    f = flow
    c = f["clients"][0]
    file = upload(f)
    body = {"version": 1, "revision": file["revision"], "category": "project_filing"}
    updated = c.patch(
        f"/api/v1/applications/{f['aid']}/attachments/{file['id']}/category",
        headers=f["headers"][0],
        json=body,
    )
    assert updated.status_code == 200 and updated.json()["material"]["revision"] == 2
    assert c.get(f"/api/v1/applications/{f['aid']}").json()["version"] == 1
    assert (
        c.patch(
            f"/api/v1/applications/{f['aid']}/attachments/{file['id']}/category",
            headers=f["headers"][0],
            json=body,
        ).status_code
        == 409
    )
    first_context = request_context(f)
    assert action(f, "submit", token=first_context["review_token"]).status_code == 200
    first_id = versions(f)[0]["id"]
    original = snapshot(f, first_id).json()
    assert original["content"]["materials"][0]["category"] == "project_filing"
    assert action(f, "reject", client=2).status_code == 200
    assert c.delete(f"/api/v1/attachments/{file['id']}", headers=f["headers"][0]).status_code == 200
    assert c.get(f"/api/v1/attachments/{file['id']}/download").status_code == 404
    assert c.get(f"/api/v1/applications/{f['aid']}/attachments").json()["items"] == []
    historical = (
        f"/api/v1/applications/{f['aid']}/submissions/{first_id}/materials/{file['id']}/download"
    )
    assert c.get(historical).content == b"%PDF-1.4 proof" and len(list(f["tmp"].iterdir())) == 1
    with SessionLocal.begin() as db:
        db.get(Station, f["sid"]).address = "second-address"
    edit = c.patch(
        f"/api/v1/applications/{f['aid']}",
        headers=f["headers"][0],
        json={
            "version": 3,
            "note": "second-note",
            "contact_name": "new-contact",
            "contact_phone": "new-phone",
        },
    )
    assert edit.status_code == 200
    upload(f, "new.pdf", b"%PDF-1.4 new proof")
    assert action(f, "submit", token=request_context(f)["review_token"]).status_code == 200
    assert len(versions(f)) == 2 and snapshot(f, first_id).json() == original
    latest = snapshot(f, versions(f)[0]["id"]).json()
    assert (
        latest["content"]["source_profile"]["address"] == "second-address"
        and latest["content"]["payload"]["note"] == "second-note"
    )
    assert latest["content"]["materials"][0]["filename"] == "new.pdf"
    packed = c.get(f"/api/v1/applications/{f['aid']}/submissions/{first_id}/archive")
    assert packed.status_code == 200 and packed.headers["cache-control"] == "no-store"
    with zipfile.ZipFile(io.BytesIO(packed.content)) as z:
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["materials"][0]["category"] == "project_filing"
        assert z.read(manifest["materials"][0]["entry"]) == b"%PDF-1.4 proof"
    with SessionLocal() as db:
        row = db.get(Attachment, file["id"])
        path = Path(settings.storage_path) / row.storage_key
    path.write_bytes(b"tampered")
    assert c.get(historical).status_code == 409
    assert (
        c.get(f"/api/v1/applications/{f['aid']}/submissions/{first_id}/archive").status_code == 409
    )


def test_source_and_material_rights_do_not_expand_through_evidence_endpoints(flow):
    f = flow
    owner, other, reg, assoc = f["clients"]
    file = upload(f)
    assert action(f, "submit").status_code == 200
    proof_id = versions(f)[0]["id"]
    paths = [
        f"/api/v1/applications/{f['aid']}/review-context",
        f"/api/v1/applications/{f['aid']}/submissions",
        f"/api/v1/applications/{f['aid']}/submissions/{proof_id}",
        f"/api/v1/applications/{f['aid']}/submissions/{proof_id}/archive",
        f"/api/v1/applications/{f['aid']}/submissions/{proof_id}/materials/{file['id']}/download",
    ]
    for path in paths:
        assert (
            other.get(path).status_code == 404
            and assoc.get(path).status_code == 403
            and reg.get(path).status_code == 200
        )
    profile_ids = []
    try:
        with SessionLocal.begin() as db:
            p = RoleProfile(
                name="proof-reader-" + secrets.token_hex(6),
                base_role="operator",
                permissions=["applications.read"],
            )
            db.add(p)
            db.flush()
            profile_ids.append(p.id)
            db.scalar(select(User).where(User.username == f["names"][0])).role_profile_id = p.id
        value = owner.get(paths[0]).json()
        assert (
            value["source_accessible"] is False
            and value["content"]["source_profile"] is None
            and value["content"]["equipment"] is None
            and value["review_token"] is None
        )
        hidden = snapshot(f, proof_id).json()
        assert hidden["content"]["source_profile"] is None and hidden["content_digest"] is None
        assert hidden["content"]["equipment"] is None
        assert hidden["content"]["payload"]["contact"]["phone"] == "APP-CONTACT-ONLY"
        with SessionLocal.begin() as db:
            db.scalar(select(User).where(User.username == f["names"][0])).role_profile_id = None
        assert action(f, "reject", client=2).status_code == 200
        with SessionLocal.begin() as db:
            p = RoleProfile(
                name="proof-writer-" + secrets.token_hex(6),
                base_role="operator",
                permissions=["applications.materials"],
            )
            db.add(p)
            db.flush()
            profile_ids.append(p.id)
            db.scalar(select(User).where(User.username == f["names"][0])).role_profile_id = p.id
        r = owner.patch(
            f"/api/v1/applications/{f['aid']}/attachments/{file['id']}/category",
            headers=f["headers"][0],
            json={"version": 3, "revision": 1, "category": "other"},
        )
        assert r.status_code == 403
        with SessionLocal.begin() as db:
            p = db.get(RoleProfile, profile_ids[-1])
            p.permissions = ["applications.read", "applications.materials"]
        r = owner.patch(
            f"/api/v1/applications/{f['aid']}/attachments/{file['id']}/category",
            headers=f["headers"][0],
            json={"version": 3, "revision": 1, "category": "other"},
        )
        assert (
            r.status_code == 200 and "payload" not in r.json() and "APP-CONTACT-ONLY" not in r.text
        )
    finally:
        with SessionLocal.begin() as db:
            db.scalar(select(User).where(User.username == f["names"][0])).role_profile_id = None
            db.flush()
            db.execute(delete(RoleProfile).where(RoleProfile.id.in_(profile_ids)))


def test_submission_projection_failure_rolls_back_evidence_and_can_retry(flow, monkeypatch):
    f = flow
    c = f["clients"][0]
    from app.routers import applications

    original = applications.named_output
    monkeypatch.setattr(
        applications, "named_output", lambda db, a: {**original(db, a), "version": "invalid"}
    )
    with pytest.raises(ValidationError):
        c.post(
            f"/api/v1/applications/{f['aid']}/actions",
            headers=f["headers"][0],
            json={"action": "submit", "version": 1, "comment": "proof"},
        )
    monkeypatch.setattr(applications, "named_output", original)
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(ApplicationSubmission)
                .where(ApplicationSubmission.application_id == f["aid"])
            )
            == 0
        )
    assert c.get(f"/api/v1/applications/{f['aid']}").json()["version"] == 1
    assert action(f, "submit", token=request_context(f)["review_token"]).status_code == 200
    assert len(versions(f)) == 1


def test_optional_contact_pair_and_old_note_patch_preservation(flow):
    f = flow
    c = f["clients"][0]
    url = f"/api/v1/applications/{f['aid']}"
    assert (
        c.patch(
            url, headers=f["headers"][0], json={"version": 1, "note": "x", "contact_name": "only"}
        ).status_code
        == 422
    )
    assert (
        c.patch(
            url,
            headers=f["headers"][0],
            json={"version": 1, "note": "x", "contact_name": "only", "contact_phone": None},
        ).status_code
        == 422
    )
    ok = c.patch(url, headers=f["headers"][0], json={"version": 1, "note": "note-only"})
    assert ok.status_code == 200 and ok.json()["payload"]["contact"]["phone"] == "APP-CONTACT-ONLY"
    assert (
        c.patch(
            url,
            headers=f["headers"][0],
            json={"version": 2, "note": "clear", "contact_name": None, "contact_phone": None},
        ).status_code
        == 200
    )
    assert c.get(url).json()["payload"]["contact"] == {"name": None, "phone": None}


@pytest.mark.parametrize("change", ["upload", "category", "remove"])
def test_material_changes_invalidate_review_without_overwriting_application_draft(flow, change):
    f = flow
    c = f["clients"][0]
    file = upload(f)
    before = request_context(f)
    if change == "upload":
        upload(f, "additional.pdf")
    elif change == "category":
        assert (
            c.patch(
                f"/api/v1/applications/{f['aid']}/attachments/{file['id']}/category",
                headers=f["headers"][0],
                json={"version": 1, "revision": 1, "category": "other"},
            ).status_code
            == 200
        )
    else:
        assert (
            c.delete(f"/api/v1/attachments/{file['id']}", headers=f["headers"][0]).status_code
            == 200
        )
    assert c.get(f"/api/v1/applications/{f['aid']}").json()["version"] == 1
    assert action(f, "submit", token=before["review_token"]).status_code == 409
    assert versions(f) == []
    fresh = request_context(f)
    assert fresh["review_token"] != before["review_token"]
    assert action(f, "submit", token=fresh["review_token"]).status_code == 200


def test_category_projection_failure_rolls_back_revision_history_and_can_retry(flow, monkeypatch):
    f = flow
    c = f["clients"][0]
    file = upload(f)
    original = evidence.material
    monkeypatch.setattr(evidence, "material", lambda row: {**original(row), "size": -1})
    path = f"/api/v1/applications/{f['aid']}/attachments/{file['id']}/category"
    body = {"version": 1, "revision": 1, "category": "other"}
    with pytest.raises(ValidationError):
        c.patch(path, headers=f["headers"][0], json=body)
    monkeypatch.setattr(evidence, "material", original)
    with SessionLocal() as db:
        row = db.get(Attachment, file["id"])
        assert row.category is None and row.revision == 1
    assert c.patch(path, headers=f["headers"][0], json=body).status_code == 200


def test_review_projection_has_one_snapshot_during_concurrent_source_payload_and_material_updates(
    flow,
):
    from sqlalchemy import event

    from app.db import engine

    f = flow
    file = upload(f)
    fired = False

    def update_after_scope(connection, cursor, statement, parameters, context, many):
        nonlocal fired
        if fired or "FROM stations" not in statement:
            return
        fired = True
        with SessionLocal.begin() as db:
            db.get(Station, f["sid"]).name = "new-concurrent-source"
            a = db.get(StationApplication, f["aid"])
            a.payload = {"note": "new-concurrent-note"}
            a.version += 1
            material = db.get(Attachment, file["id"])
            material.category = "other"
            material.revision += 1

    event.listen(engine, "after_cursor_execute", update_after_scope)
    try:
        first = request_context(f)
    finally:
        event.remove(engine, "after_cursor_execute", update_after_scope)
    assert fired and first["application_version"] == 1
    assert first["content"]["source_profile"]["station_name"] == "隔离测试"
    assert (
        first["content"]["payload"]["note"] == "initial-note"
        and first["content"]["materials"][0]["category"] is None
    )
    second = request_context(f)
    assert (
        second["application_version"] == 2
        and second["content"]["source_profile"]["station_name"] == "new-concurrent-source"
    )
    assert (
        second["content"]["payload"]["note"] == "new-concurrent-note"
        and second["content"]["materials"][0]["category"] == "other"
    )


def test_equipment_and_connector_specs_are_frozen_and_invalidate_review(flow):
    from app.models import Connector, Equipment

    with SessionLocal.begin() as db:
        e = Equipment(
            station_id=flow["sid"], external_id="proof-device", name="原设备", power_kw=60
        )
        db.add(e)
        db.flush()
        equipment_id = e.id
        c = Connector(
            equipment_id=e.id, external_id="proof-plug", name="原枪口", interface_type="GB/T"
        )
        db.add(c)
        db.flush()
        connector_id = c.id
    try:
        first = request_context(flow)
        assert first["content"]["schema_version"] == 2
        assert first["content"]["equipment"][0]["name"] == "原设备"
        with SessionLocal.begin() as db:
            db.get(Connector, connector_id).status = "charging"
        assert request_context(flow)["review_token"] == first["review_token"]
        with SessionLocal.begin() as db:
            db.get(Equipment, equipment_id).model_name = "更新型号"
        assert action(flow, "submit", token=first["review_token"]).status_code == 409
        current = request_context(flow)
        assert action(flow, "submit", token=current["review_token"]).status_code == 200
        with SessionLocal.begin() as db:
            row = db.scalar(
                select(ApplicationSubmission).where(
                    ApplicationSubmission.application_id == flow["aid"]
                )
            )
            frozen = json.loads(json.dumps(row.snapshot))
            db.get(Equipment, equipment_id).name = "同步后的设备"
            db.get(Connector, connector_id).name = "同步后的枪口"
        with SessionLocal() as db:
            row = db.scalar(
                select(ApplicationSubmission).where(
                    ApplicationSubmission.application_id == flow["aid"]
                )
            )
            assert row.snapshot == frozen
            assert row.snapshot["equipment"][0]["connectors"][0]["name"] == "原枪口"
        assert (
            evidence.ReviewContent.model_validate(
                {"schema_version": 1, "source_profile": None, "payload": {}, "materials": []}
            ).equipment
            is None
        )
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(Connector).where(Connector.id == connector_id))
            db.execute(delete(Equipment).where(Equipment.id == equipment_id))
