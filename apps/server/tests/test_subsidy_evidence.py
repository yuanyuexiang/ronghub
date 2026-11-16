# ruff: noqa: F811 - pytest fixture parameters shadow fixture imports
import io
import secrets
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from test_business_exports import login, report_staff  # noqa: F401
from test_subsidy import finance  # noqa: F401
from test_subsidy_batch import submitted  # noqa: F401

from app import subsidy
from app import subsidy_evidence as evidence
from app.config import settings
from app.db import SessionLocal
from app.models import Audit, LoginSession, SubsidyAction, SubsidyAttachment, SubsidyClaim, User


def zipped():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as bundle:
        bundle.writestr(
            zipfile.ZipInfo("说明.txt", date_time=(1980, 1, 1, 0, 0, 0)), "模拟申报资料"
        )
    return stream.getvalue()


def headers(client):
    return {"x-csrf-token": client.get("/api/v1/auth/me").json()["csrf_token"]}


@pytest.fixture
def draft(finance, accounts):
    client = login(accounts[0][0], accounts[1])
    response = client.post(
        "/api/v1/subsidy/claims",
        headers=headers(client),
        json={
            "station_id": finance[1],
            "policy_id": finance[2],
            "start_date": "2026-11-01",
            "end_date": "2026-11-02",
        },
    )
    assert response.status_code == 201, response.text
    claim = response.json()
    yield client, f"/api/v1/subsidy/claims/{claim['id']}", claim
    client.close()


def upload(client, path, version):
    return client.post(
        path + "/attachments",
        headers=headers(client),
        data={"version": str(version)},
        files={"file": ("申报资料.zip", zipped(), "application/zip")},
    )


def note(client, path, version, text="申请说明"):
    return client.patch(
        path, headers=headers(client), json={"version": version, "application_note": text}
    )


def submit(client, path, version):
    return client.post(
        path + "/actions", headers=headers(client), json={"action": "submit", "version": version}
    )


def test_statement_and_zip_are_required_versioned_frozen_and_private(draft, accounts, report_staff):
    client, path, _claim = draft
    assert submit(client, path, 1).status_code == 422
    assert note(client, path, 1, "申请说明A").json()["version"] == 2
    assert submit(client, path, 2).status_code == 422
    wrong = client.post(
        path + "/attachments",
        headers=headers(client),
        data={"version": "2"},
        files={"file": ("wrong.pdf", b"%PDF-1.4 proof", "application/pdf")},
    )
    assert wrong.status_code == 415
    result = upload(client, path, 2)
    assert result.status_code == 201, result.text
    material = result.json()["material"]
    assert result.json()["version"] == 3
    assert upload(client, path, 2).status_code == 409
    listing = client.get(path + "/attachments").json()
    assert listing["version"] == 3 and listing["items"] == [material]
    assert listing["total_bytes"] == len(zipped())
    download = path + f"/attachments/{material['id']}/download"
    assert "storage_key" not in material
    assert login(accounts[0][1], accounts[1]).get(download).status_code == 404
    assert login(report_staff[0][1], report_staff[1]).get(download).status_code == 403
    regulator = login(report_staff[0][0], report_staff[1])
    content = regulator.get(download)
    assert content.status_code == 200 and content.content == zipped()
    assert (
        content.headers["content-type"] == "application/zip"
        and content.headers["cache-control"] == "no-store"
    )
    assert note(client, path, 2).status_code == 409
    assert note(client, path, 3, "申请说明B").json()["version"] == 4
    assert submit(client, path, 4).status_code == 200
    detail = client.get(path).json()
    packet = detail["snapshot"]["submission"]
    assert packet["note"] == "申请说明B" and packet["version"] == 5
    assert packet["materials"] == [{**material, "submitted_version": 5}]
    assert note(client, path, 5).status_code == 409
    assert upload(client, path, 5).status_code == 409
    assert (
        client.delete(
            path + f"/attachments/{material['id']}", params={"version": 5}, headers=headers(client)
        ).status_code
        == 409
    )
    approved = regulator.post(
        path + "/actions",
        headers=headers(regulator),
        json={"action": "approve", "version": 5, "comment": "已核验材料"},
    )
    assert approved.status_code == 200, approved.text
    assert client.get(path).json()["snapshot"]["submission"] == packet
    assert client.get(path).json()["base_amount"] == "3.02"


@pytest.mark.parametrize("damage", ["missing", "digest", "symlink", "key"])
def test_missing_or_corrupt_evidence_blocks_submission_without_partial_freeze(draft, damage):
    client, path, claim = draft
    assert note(client, path, 1).status_code == 200
    uploaded = upload(client, path, 2).json()
    with SessionLocal.begin() as db:
        record = db.get(SubsidyAttachment, uploaded["material"]["id"])
        file = Path(settings.storage_path) / record.storage_key
        if damage == "missing":
            file.unlink()
        elif damage == "digest":
            file.write_bytes(b"x" * record.size)
        elif damage == "symlink":
            target = file.parent / "original-file"
            file.rename(target)
            file.symlink_to(target)
        else:
            record.storage_key = "../outside"
    response = submit(client, path, 3)
    assert response.status_code == 409, response.text
    with SessionLocal() as db:
        record = db.get(SubsidyAttachment, uploaded["material"]["id"])
        current = db.get(SubsidyClaim, claim["id"])
        assert (
            record.submitted_version is None and current.version == 3 and current.status == "draft"
        )
        assert "submission" not in current.snapshot
        assert (
            db.scalar(
                select(func.count())
                .select_from(SubsidyAction)
                .where(SubsidyAction.claim_id == claim["id"], SubsidyAction.action == "submit")
            )
            == 0
        )


@pytest.mark.parametrize("stage", ["upload", "edit", "submit"])
def test_projection_failure_rolls_back_notes_files_versions_and_freeze(draft, monkeypatch, stage):
    client, path, claim = draft
    if stage == "submit":
        assert note(client, path, 1).status_code == 200
        assert upload(client, path, 2).status_code == 201
    before = client.get(path).json()
    files_before = (
        list(Path(settings.storage_path).iterdir()) if Path(settings.storage_path).exists() else []
    )
    if stage == "upload":
        original = evidence.metadata
        monkeypatch.setattr(evidence, "metadata", lambda record: {**original(record), "size": -1})
    else:
        original = subsidy.output
        monkeypatch.setattr(
            subsidy, "output", lambda record: {**original(record), "base_amount": 3.02}
        )
    with pytest.raises(ValidationError):
        if stage == "upload":
            upload(client, path, 1)
        elif stage == "edit":
            note(client, path, 1, "未能提交的说明")
        else:
            submit(client, path, 3)
    if stage == "upload":
        monkeypatch.setattr(evidence, "metadata", original)
    else:
        monkeypatch.setattr(subsidy, "output", original)
    assert client.get(path).json() == before
    files_after = (
        list(Path(settings.storage_path).iterdir()) if Path(settings.storage_path).exists() else []
    )
    assert files_after == files_before
    with SessionLocal() as db:
        assert all(
            record.submitted_version is None
            for record in db.scalars(
                select(SubsidyAttachment).where(SubsidyAttachment.claim_id == claim["id"])
            )
        )


def test_legacy_claim_does_not_invent_note_or_submission_packet(draft):
    client, path, claim = draft
    with SessionLocal.begin() as db:
        record = db.get(SubsidyClaim, claim["id"])
        record.application_note = None
        record.status = "approved"
    detail = client.get(path).json()
    assert detail["application_note"] is None and detail["snapshot"]["submission"] is None
    assert client.get(path + "/attachments").json()["items"] == []


def test_frozen_file_failure_rolls_back_an_entire_batch_review(submitted, report_staff):
    client = login(report_staff[0][0], report_staff[1])
    with SessionLocal() as db:
        record = db.scalar(
            select(SubsidyAttachment).where(SubsidyAttachment.claim_id == max(submitted))
        )
        (Path(settings.storage_path) / record.storage_key).write_bytes(b"x" * record.size)
    response = client.post(
        "/api/v1/subsidy/claims/batch-actions",
        headers=headers(client),
        json={
            "action": "approve",
            "items": [{"id": cid, "version": 2} for cid in submitted],
            "comment": "批量核验",
        },
    )
    assert response.status_code == 409 and "整批未提交" in response.text
    with SessionLocal() as db:
        assert all(
            record.status == "submitted" and record.version == 2
            for record in db.scalars(select(SubsidyClaim).where(SubsidyClaim.id.in_(submitted)))
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(SubsidyAction)
                .where(SubsidyAction.claim_id.in_(submitted), SubsidyAction.action == "approve")
            )
            == 0
        )


def test_competing_uploads_use_claim_versions_across_different_accounts(
    draft, finance, accounts, monkeypatch
):
    client, path, claim = draft
    with SessionLocal.begin() as db:
        owner = db.get(User, finance[0])
        user = User(
            username="evidence-" + secrets.token_hex(8),
            password_hash=owner.password_hash,
            operator_id=owner.operator_id,
            role="operator",
        )
        db.add(user)
        db.flush()
        uid, name = user.id, user.username
    other = login(name, accounts[1])
    clients = [client, other]
    tokens = [headers(client), headers(other)]
    barrier = Barrier(2)
    from app.routers import subsidy_materials

    original = subsidy_materials.authorize_business_write

    def access(*args, **kwargs):
        barrier.wait(timeout=10)
        return original(*args, **kwargs)

    monkeypatch.setattr(subsidy_materials, "authorize_business_write", access)
    try:

        def attempt(index):
            return clients[index].post(
                path + "/attachments",
                headers=tokens[index],
                data={"version": "1"},
                files={"file": ("资料.zip", zipped(), "application/zip")},
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(attempt, [0, 1]))
        assert sorted(response.status_code for response in responses) == [201, 409]
        with SessionLocal() as db:
            assert db.get(SubsidyClaim, claim["id"]).version == 2
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(SubsidyAttachment)
                    .where(SubsidyAttachment.claim_id == claim["id"])
                )
                == 1
            )
    finally:
        other.close()
        with SessionLocal.begin() as db:
            db.execute(delete(SubsidyAttachment).where(SubsidyAttachment.uploaded_by == uid))
            db.execute(delete(SubsidyAction).where(SubsidyAction.actor_id == uid))
            db.execute(delete(Audit).where(Audit.user_id == uid))
            db.execute(delete(LoginSession).where(LoginSession.user_id == uid))
            db.execute(delete(User).where(User.id == uid))


def test_material_edit_submit_and_read_grants_are_independent(draft, finance, accounts):
    from app.authorization import normalize_grants
    from app.models import RoleProfile

    client, path, _claim = draft
    assert note(client, path, 1).status_code == 200
    with SessionLocal.begin() as db:
        profile = RoleProfile(
            name="evidence-role-" + secrets.token_hex(8),
            base_role="operator",
            permissions=["subsidy.read", "subsidy.materials"],
            version=1,
        )
        db.add(profile)
        db.flush()
        pid = profile.id
        db.get(User, finance[0]).role_profile_id = pid
    try:
        assert note(client, path, 2).status_code == 403
        assert upload(client, path, 2).status_code == 201
        assert submit(client, path, 3).status_code == 403
        with SessionLocal.begin() as db:
            db.get(RoleProfile, pid).permissions = ["subsidy.read", "subsidy.submit"]
        assert upload(client, path, 3).status_code == 403
        assert note(client, path, 3).status_code == 403
        assert submit(client, path, 3).status_code == 200
        with SessionLocal.begin() as db:
            db.get(RoleProfile, pid).permissions = ["subsidy.materials"]
        assert upload(client, path, 4).status_code == 403
        assert client.get(path + "/attachments").status_code == 403
        with SessionLocal.begin() as db:
            db.get(RoleProfile, pid).permissions = ["subsidy.read", "subsidy.materials"]
            db.get(User, finance[0]).allowed_districts = []
        assert client.get(path + "/attachments").status_code == 404
        assert not {"subsidy.edit", "subsidy.materials"}.intersection(
            normalize_grants("operator", ["subsidy.read", "subsidy.write"])
        )
    finally:
        with SessionLocal.begin() as db:
            db.get(User, finance[0]).role_profile_id = None
            db.execute(delete(RoleProfile).where(RoleProfile.id == pid))


@pytest.mark.parametrize("invalid", ["\x00", "x" * 2001])
def test_invalid_statement_cannot_update_draft(draft, invalid):
    client, path, _claim = draft
    response = note(client, path, 1, invalid)
    assert response.status_code == 422, response.text
    assert client.get(path).json()["version"] == 1


def test_download_finishes_capturing_bytes_before_a_concurrent_delete(draft, monkeypatch):
    import threading
    import time

    from sqlalchemy import text

    client, path, _claim = draft
    material = upload(client, path, 1).json()["material"]
    # A second independent session for the same operator; the GET does not hold the company writer lock.
    from fastapi.testclient import TestClient

    from app.main import app

    second = TestClient(app)
    second.cookies.update(client.cookies)
    tokens = headers(second)
    reading = threading.Event()
    release = threading.Event()
    deleting = threading.Event()
    pids = []
    original_content = evidence.content
    original_access = subsidy.claim_access

    def held(record):
        reading.set()
        assert release.wait(10)
        return original_content(record)

    def captured(db, user, cid, lock=False):
        if reading.is_set() and lock:
            pids.append(db.scalar(text("SELECT pg_backend_pid()")))
            deleting.set()
        return original_access(db, user, cid, lock)

    monkeypatch.setattr(evidence, "content", held)
    monkeypatch.setattr(subsidy, "claim_access", captured)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            downloaded = pool.submit(client.get, path + f"/attachments/{material['id']}/download")
            try:
                assert reading.wait(5)
                removal = pool.submit(
                    second.delete,
                    path + f"/attachments/{material['id']}",
                    params={"version": 2},
                    headers=tokens,
                )
                assert deleting.wait(5)
                deadline = time.monotonic() + 5
                locked = False
                while time.monotonic() < deadline:
                    with SessionLocal() as db:
                        locked = db.scalar(
                            text(
                                "SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=:pid"
                            ),
                            {"pid": pids[0]},
                        )
                    if locked:
                        break
                    time.sleep(0.02)
                assert locked, "delete must wait on PostgreSQL's real claim lock"
            finally:
                release.set()
            response = downloaded.result(timeout=5)
            assert response.status_code == 200 and response.content == zipped()
            assert removal.result(timeout=5).status_code == 200
    finally:
        second.close()


def test_upload_count_and_total_limits_preserve_version_and_existing_file(draft, monkeypatch):
    client, path, _claim = draft
    assert upload(client, path, 1).status_code == 201
    monkeypatch.setattr(evidence, "MAX_FILES", 1)
    assert upload(client, path, 2).status_code == 422
    monkeypatch.setattr(evidence, "MAX_FILES", 10)
    monkeypatch.setattr(evidence, "LIMIT", len(zipped()))
    assert upload(client, path, 2).status_code == 413
    current = client.get(path + "/attachments").json()
    assert current["version"] == 2 and len(current["items"]) == 1
    assert len(list(Path(settings.storage_path).iterdir())) == 1
