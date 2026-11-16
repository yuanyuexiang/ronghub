import base64
import io
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

from app import captcha
from app.config import settings
from app.db import SessionLocal
from app.dependencies import token_hash
from app.main import app
from app.models import CaptchaChallenge, LoginSession, User, now

pytestmark = pytest.mark.captcha_manual


def client():
    return TestClient(app, client=("captcha-" + secrets.token_hex(8), 1))


def test_captcha_required_bound_single_use_refresh_expiry_and_failed_password(
    accounts, monkeypatch
):
    monkeypatch.setattr(captcha, "generate_answer", lambda: "ABCDE")
    own, other = client(), client()
    try:
        credentials = {"username": accounts[0][0], "password": accounts[1]}
        assert own.post("/api/v1/auth/login", json=credentials).status_code == 422
        issued = own.get("/api/v1/auth/captcha")
        assert issued.status_code == 200 and issued.headers["cache-control"] == "no-store"
        assert "HttpOnly" in issued.headers["set-cookie"]
        data = issued.json()
        assert set(data) == {"captcha_id", "image", "expires_at", "expires_in_seconds"}
        assert data["expires_in_seconds"] == 120
        image = Image.open(io.BytesIO(base64.b64decode(data["image"].split(",", 1)[1])))
        assert image.size == (190, 66) and image.format == "PNG" and not image.info
        payload = {**credentials, "captcha_id": data["captcha_id"], "captcha_answer": "abcde"}
        assert other.post("/api/v1/auth/login", json=payload).status_code == 422
        assert own.post("/api/v1/auth/login", json=payload).status_code == 200
        assert own.post("/api/v1/auth/login", json=payload).status_code == 422
        fresh = own.get("/api/v1/auth/captcha").json()
        payload.update(captcha_id=fresh["captcha_id"], password="incorrect")
        assert own.post("/api/v1/auth/login", json=payload).status_code == 401
        payload["password"] = accounts[1]
        assert own.post("/api/v1/auth/login", json=payload).status_code == 422
        old = own.get("/api/v1/auth/captcha").json()
        fresh = own.get("/api/v1/auth/captcha").json()
        payload["captcha_id"] = old["captcha_id"]
        assert own.post("/api/v1/auth/login", json=payload).status_code == 422
        payload["captcha_id"] = fresh["captcha_id"]
        payload["captcha_answer"] = "wrong"
        assert own.post("/api/v1/auth/login", json=payload).status_code == 422
        payload["captcha_answer"] = "ABCDE"
        assert own.post("/api/v1/auth/login", json=payload).status_code == 422
        fresh = own.get("/api/v1/auth/captcha").json()
        with SessionLocal.begin() as db:
            row = db.get(CaptchaChallenge, token_hash(fresh["captcha_id"]))
            assert row.answer_hash == token_hash(fresh["captcha_id"] + ":ABCDE")
            row.expires_at = now() - timedelta(seconds=1)
        payload["captcha_id"] = fresh["captcha_id"]
        assert own.post("/api/v1/auth/login", json=payload).status_code == 422
    finally:
        own.close()
        other.close()


def test_concurrent_same_challenge_creates_one_session(accounts, monkeypatch):
    monkeypatch.setattr(captcha, "generate_answer", lambda: "ABCDE")
    own = client()
    others = []
    try:
        data = own.get("/api/v1/auth/captcha").json()
        for _ in range(2):
            c = client()
            c.cookies.update(own.cookies)
            others.append(c)
        barrier = Barrier(2)
        payload = {
            "username": accounts[0][0],
            "password": accounts[1],
            "captcha_id": data["captcha_id"],
            "captcha_answer": "ABCDE",
        }

        def submit(c):
            barrier.wait(timeout=5)
            return c.post("/api/v1/auth/login", json=payload).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, others))
        assert sorted(results) == [200, 422]
        with SessionLocal() as db:
            uid = db.scalar(select(User.id).where(User.username == accounts[0][0]))
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(LoginSession)
                    .where(LoginSession.user_id == uid)
                )
                == 1
            )
    finally:
        own.close()
        for c in others:
            c.close()


def test_captcha_persistent_issue_and_verify_limits(monkeypatch):
    monkeypatch.setattr(captcha, "generate_answer", lambda: "ABCDE")
    own = client()
    other = client()
    try:
        for _ in range(60):
            assert own.get("/api/v1/auth/captcha").status_code == 200
        blocked = own.get("/api/v1/auth/captcha")
        assert blocked.status_code == 429 and blocked.headers["retry-after"] == "300"
        payload = {
            "username": "unlisted",
            "password": "invalid",
            "captcha_id": "x" * 43,
            "captcha_answer": "ABCDE",
        }
        for _ in range(60):
            assert other.post("/api/v1/auth/login", json=payload).status_code == 422
        assert other.post("/api/v1/auth/login", json=payload).status_code == 429
    finally:
        own.close()
        other.close()


def test_production_login_also_requires_captcha_and_sets_secure_binding(accounts, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(captcha, "generate_answer", lambda: "ABCDE")
    with TestClient(
        app, base_url="https://testserver", client=("captcha-prod-" + secrets.token_hex(8), 1)
    ) as own:
        credentials = {"username": accounts[0][0], "password": accounts[1]}
        assert own.post("/api/v1/auth/login", json=credentials).status_code == 422
        issued = own.get("/api/v1/auth/captcha")
        assert "; Secure" in issued.headers["set-cookie"]
        result = own.post(
            "/api/v1/auth/login",
            json={
                **credentials,
                "captcha_id": issued.json()["captcha_id"],
                "captcha_answer": "ABCDE",
            },
        )
        assert result.status_code == 200, result.text
