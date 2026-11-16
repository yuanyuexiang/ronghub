import secrets

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import User


def client():
    return TestClient(app, client=("test-" + secrets.token_hex(5), 5000))


def login(c, username, password):
    r = c.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return {"x-csrf-token": r.json()["csrf_token"]}


def test_login_limit_origin_and_disabled_session(accounts):
    names, password, _ = accounts
    with client() as c:
        assert (
            c.post(
                "/api/v1/auth/login",
                headers={"Origin": "https://evil.invalid"},
                json={"username": names[0], "password": password},
            ).status_code
            == 403
        )
        for _ in range(5):
            assert (
                c.post(
                    "/api/v1/auth/login", json={"username": names[0], "password": "wrong"}
                ).status_code
                == 401
            )
        r = c.post("/api/v1/auth/login", json={"username": names[0], "password": password})
        assert r.status_code == 429 and r.headers["Retry-After"] == "900"
    with client() as c:
        login(c, names[1], password)
        with SessionLocal.begin() as db:
            db.scalar(select(User).where(User.username == names[1])).enabled = False
        assert c.get("/api/v1/auth/me").status_code == 401


def test_material_upload_access_and_frozen_submission(accounts, tmp_path, monkeypatch):
    names, password, stations = accounts
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    with client() as a, client() as b:
        ah = login(a, names[0], password)
        bh = login(b, names[1], password)
        r = a.post(
            "/api/v1/applications", headers=ah, json={"station_id": stations[0], "kind": "access"}
        )
        assert r.status_code == 201
        application = r.json()
        aid = application["id"]
        updated = a.patch(
            f"/api/v1/applications/{aid}", headers=ah, json={"version": 1, "note": "材料核对说明"}
        )
        assert updated.status_code == 200 and updated.json()["version"] == 2
        assert (
            a.patch(
                f"/api/v1/applications/{aid}", headers=ah, json={"version": 1, "note": "过期编辑"}
            ).status_code
            == 409
        )
        url = f"/api/v1/applications/{aid}/attachments"
        assert (
            a.post(
                url, headers=ah, files={"file": ("bad.html", b"<html>bad</html>", "text/html")}
            ).status_code
            == 415
        )
        content = b"%PDF-1.4\nDemonstration material"
        r = a.post(url, headers=ah, files={"file": ("../../proof.pdf", content, "application/pdf")})
        assert r.status_code == 201
        attachment = r.json()
        assert attachment["filename"] == "proof.pdf"
        file_url = f"/api/v1/attachments/{attachment['id']}/download"
        assert a.get(file_url).content == content
        assert a.get(file_url).headers["Cache-Control"] == "no-store"
        assert b.get(file_url).status_code == 404
        assert b.get(url).status_code == 404
        assert b.post(url, headers=bh, files={"file": ("proof.pdf", content)}).status_code == 404
        assert (
            a.post(
                url, headers=ah, files={"file": ("large.pdf", b"%PDF-" + b"x" * (5 * 1024 * 1024))}
            ).status_code
            == 413
        )
        assert len(list(tmp_path.iterdir())) == 1
        assert (
            a.post(
                f"/api/v1/applications/{aid}/actions",
                headers=ah,
                json={"action": "submit", "version": 2},
            ).status_code
            == 200
        )
        assert a.post(url, headers=ah, files={"file": ("proof.pdf", content)}).status_code == 409
        assert a.delete(f"/api/v1/attachments/{attachment['id']}", headers=ah).status_code == 409
