# ruff: noqa: F811 - pytest resolves imported fixtures by parameter name
import hashlib
import io
import json
import secrets
import stat
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, func, select, text
from test_business_exports import login
from test_rating import rating_fixture  # noqa: F401

from app import material_archive, rating
from app.config import settings
from app.db import SessionLocal
from app.models import Audit, RatingAttachment, RoleProfile, User
from app.routers import rating_materials


@pytest.fixture
def materials(rating_fixture, accounts, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    uid, station, standard = rating_fixture
    with SessionLocal.begin() as db:
        application = rating.create(
            db, db.get(User, uid), station, standard, {"service": "90"}, "材料"
        )
        aid = application.id
    client = login(accounts[0][0], accounts[1])
    second = login(accounts[0][0], accounts[1])
    headers = {"x-csrf-token": client.get("/api/v1/auth/me").json()["csrf_token"]}
    second_headers = {"x-csrf-token": second.get("/api/v1/auth/me").json()["csrf_token"]}
    url = f"/api/v1/ratings/applications/{aid}/attachments"
    yield client, second, headers, second_headers, url, aid, uid, tmp_path
    client.close()
    second.close()
    with SessionLocal.begin() as db:
        db.execute(delete(RatingAttachment).where(RatingAttachment.application_id == aid))


def upload(fixture, name="材料.pdf", content=b"%PDF-1.4 original"):
    client, _, headers, _, url, *_ = fixture
    response = client.post(
        url, headers=headers, data={"category": "site_plan"}, files={"file": (name, content)}
    )
    assert response.status_code == 201, response.text
    return response.json()


def audit_count(aid):
    with SessionLocal() as db:
        return db.scalar(
            select(func.count())
            .select_from(Audit)
            .where(Audit.object_id == aid, Audit.action == "ratings.materials.archive")
        )


def inspect_archive(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "no-store"
    bundle = zipfile.ZipFile(io.BytesIO(response.content))
    manifest = json.loads(bundle.read("manifest.json"))
    assert len(bundle.namelist()) == len(set(bundle.namelist())) == len(manifest["materials"]) + 1
    for entry in bundle.infolist():
        assert not entry.filename.startswith("/") and ".." not in entry.filename.split("/")
        assert entry.compress_type == zipfile.ZIP_STORED
        assert stat.S_IMODE(entry.external_attr >> 16) == 0o600
    for material in manifest["materials"]:
        content = bundle.read(material["entry"])
        assert len(content) == material["size"]
        assert hashlib.sha256(content).hexdigest() == material["sha256"]
    return bundle, manifest


def test_archive_scope_determinism_and_frozen_manifest(materials, accounts, staff):
    client, _, headers, _, url, aid, uid, path = materials
    assert client.get(url + "/archive").status_code == 409
    assert audit_count(aid) == 0
    original = b"%PDF-1.4 original"
    first = upload(materials, "同名材料.pdf", original)
    upload(materials, "同名材料.pdf", original + b"2")
    opaque = b"PK\x03\x04opaque-not-extracted"
    upload(materials, "原始包.zip", opaque)
    other = login(accounts[0][1], accounts[1])
    assert other.get(url + "/archive").status_code == 404
    from fastapi.testclient import TestClient

    from app.main import app

    assert TestClient(app).get(url + "/archive").status_code == 401
    for username in staff[0]:
        reader = login(username, staff[1])
        inspect_archive(reader.get(url + "/archive"))
        with SessionLocal.begin() as db:
            user = db.scalar(select(User).where(User.username == username))
            user.allowed_districts = []
        assert reader.get(url + "/archive").status_code == 404
        reader.close()
    with SessionLocal.begin() as db:
        profile = RoleProfile(
            name="archive-readonly-" + secrets.token_hex(8),
            base_role="operator",
            permissions=["stations.read", "ratings.read"],
            version=1,
        )
        db.add(profile)
        db.flush()
        profile_id = profile.id
        db.get(User, uid).role_profile_id = profile_id
    try:
        result = client.get(url + "/archive")
        bundle, manifest = inspect_archive(result)
        assert client.get(url + "/archive").content == result.content
        assert manifest["application_version"] == 1
        assert bundle.read(manifest["materials"][2]["entry"]) == opaque
        assert all(m["submitted_version"] is None for m in manifest["materials"])
        assert str(path).encode() not in result.content
        assert b"storage_key" not in bundle.read("manifest.json")
    finally:
        with SessionLocal.begin() as db:
            db.get(User, uid).role_profile_id = None
            db.execute(delete(RoleProfile).where(RoleProfile.id == profile_id))
    response = client.post(
        url.rsplit("/", 1)[0] + "/actions", headers=headers, json={"action": "submit", "version": 1}
    )
    assert response.status_code == 200, response.text
    _, manifest = inspect_archive(client.get(url + "/archive"))
    assert manifest["application_version"] == 2
    assert all(m["submitted_version"] == 2 for m in manifest["materials"])
    assert client.delete(url + f"/{first['id']}", headers=headers).status_code == 409
    with SessionLocal() as db:
        audit = db.scalar(
            select(Audit)
            .where(Audit.object_id == aid, Audit.action == "ratings.materials.archive")
            .order_by(Audit.id.desc())
        )
        assert audit.detail == {
            "files": 3,
            "bytes": len(original) * 2 + 1 + len(opaque),
            "application_version": 2,
        }
    other.close()


@pytest.mark.parametrize("damage", ["missing", "digest", "symlink", "key", "limit", "count"])
def test_archive_rejects_whole_bundle(materials, damage):
    client, _, _, _, url, aid, _, base = materials
    upload(materials)
    with SessionLocal.begin() as db:
        record = db.scalar(select(RatingAttachment).where(RatingAttachment.application_id == aid))
        path = base / record.storage_key
        if damage == "missing":
            path.unlink()
        elif damage == "digest":
            path.write_bytes(b"x" * record.size)
        elif damage == "symlink":
            target = base / "outside-file"
            path.rename(target)
            path.symlink_to(target)
        elif damage == "key":
            record.storage_key = "../outside-file"
        elif damage == "limit":
            record.size = rating_materials.LIMIT + 1
        elif damage == "count":
            for _ in range(30):
                db.add(
                    RatingAttachment(
                        application_id=aid,
                        uploaded_by=record.uploaded_by,
                        filename=record.filename,
                        content_type=record.content_type,
                        size=record.size,
                        sha256=record.sha256,
                        storage_key=secrets.token_hex(24),
                        category="site_plan",
                    )
                )
    response = client.get(url + "/archive")
    assert response.status_code == 409, response.text
    assert "content-disposition" not in response.headers
    assert not response.content.startswith(b"PK")
    assert audit_count(aid) == 0


def test_upload_projection_failure_rolls_back_file_and_row(materials, monkeypatch):
    client, _, headers, _, url, aid, _, base = materials
    original = rating_materials.item
    monkeypatch.setattr(rating_materials, "item", lambda record: {**original(record), "size": -1})
    with pytest.raises(ValidationError):
        client.post(
            url,
            headers=headers,
            data={"category": "site_plan"},
            files={"file": ("file.pdf", b"%PDF-1.4 example")},
        )
    assert not list(base.iterdir())
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(RatingAttachment)
                .where(RatingAttachment.application_id == aid)
            )
            == 0
        )


def test_archive_holds_same_application_lock_as_delete(materials, monkeypatch):
    client, second, _, second_headers, url, _, _, _ = materials
    record = upload(materials)
    reading, release, deleting = threading.Event(), threading.Event(), threading.Event()
    original_read, original_write = material_archive.read_material, rating_materials.writable
    backend_pid = []

    def blocked_read(*args):
        reading.set()
        assert release.wait(10)
        return original_read(*args)

    def capture_pid(db, *args):
        backend_pid.append(db.scalar(text("SELECT pg_backend_pid()")))
        deleting.set()
        return original_write(db, *args)

    monkeypatch.setattr(material_archive, "read_material", blocked_read)
    monkeypatch.setattr(rating_materials, "writable", capture_pid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        download = pool.submit(client.get, url + "/archive")
        try:
            assert reading.wait(5)
            removal = pool.submit(second.delete, url + f"/{record['id']}", headers=second_headers)
            assert deleting.wait(5)
            deadline = time.monotonic() + 5
            locked = False
            while time.monotonic() < deadline:
                with SessionLocal() as db:
                    locked = db.scalar(
                        text(
                            "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"
                        ),
                        {"pid": backend_pid[0]},
                    )
                if locked:
                    break
                time.sleep(0.02)
            assert locked, "delete must wait on the real PostgreSQL application/station lock"
        finally:
            release.set()
        inspect_archive(download.result(timeout=5))
        assert removal.result(timeout=5).status_code == 200
    assert client.get(url).json()["items"] == []
