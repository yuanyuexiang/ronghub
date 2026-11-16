import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from app import sms
from app.config import Settings, settings
from app.db import SessionLocal
from app.dependencies import token_hash
from app.main import app
from app.models import Audit, Operator, SmsChallenge, User, VerifiedPhone, now


@pytest.fixture()
def demo_sender(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "sms_mode", "demo")
    captured = {}

    def send(self, phone, code, purpose):
        captured[phone] = code
        return secrets.token_urlsafe(24)

    monkeypatch.setattr(sms.DemoSmsAdapter, "send", send)
    return captured


def login(accounts, index=0):
    client = TestClient(app, client=("phone-" + secrets.token_hex(8), 1))
    response = client.post(
        "/api/v1/auth/login", json={"username": accounts[0][index], "password": accounts[1]}
    )
    assert response.status_code == 200
    return client, {"x-csrf-token": response.json()["csrf_token"]}


def number():
    return "139" + f"{secrets.randbelow(100000000):08d}"


def issue(client, headers, phone, password):
    return client.post(
        "/api/v1/auth/phone/challenges",
        headers=headers,
        json={"phone": phone, "current_password": password},
    )


def confirm(client, headers, token, code):
    return client.post(
        "/api/v1/auth/phone/confirm", headers=headers, json={"challenge_id": token, "code": code}
    )


def test_binding_is_independent_private_unique_and_revokes_all_sessions(accounts, demo_sender):
    own, headers = login(accounts)
    second, _ = login(accounts)
    other, other_headers = login(accounts, 1)
    phone = number()
    try:
        with SessionLocal.begin() as db:
            user = db.scalar(select(User).where(User.username == accounts[0][0]))
            uid = user.id
            db.get(Operator, user.operator_id).contacts = [{"name": "业务联系人", "phone": phone}]
        assert own.get("/api/v1/auth/phone").json()["verified"] is False
        assert issue(own, {}, phone, accounts[1]).status_code == 403
        assert issue(own, headers, phone, "wrong password").status_code == 403
        sent = issue(own, headers, "+86 " + phone, accounts[1])
        assert sent.status_code == 201, sent.text
        data = sent.json()
        assert data["delivery_mode"] == "demo" and "未发送真实短信" in data["message"]
        assert "code" not in data and "phone" not in data
        token = data["challenge_id"]
        assert issue(own, headers, phone, accounts[1]).status_code == 429
        assert confirm(other, other_headers, token, demo_sender[phone]).status_code == 422
        result = confirm(own, headers, token, demo_sender[phone])
        assert result.status_code == 200, result.text
        assert result.json()["phone_masked"] == sms.mask(phone)
        assert own.get("/api/v1/auth/me").status_code == 401
        assert second.get("/api/v1/auth/me").status_code == 401
        assert other.get("/api/v1/auth/phone").json()["verified"] is False
        own.close()
        own, headers = login(accounts)
        state = own.get("/api/v1/auth/phone").json()
        assert state["verified"] is True and state["phone_masked"] == sms.mask(phone)
        assert phone not in own.get("/api/v1/auth/phone").text
        assert confirm(own, headers, token, demo_sender[phone]).status_code == 422
        assert issue(other, other_headers, phone, accounts[1]).status_code == 409
        assert issue(own, headers, number(), accounts[1]).status_code == 409
        with SessionLocal() as db:
            binding = db.get(VerifiedPhone, uid)
            assert binding.phone == phone and binding.version == 1
            assert db.scalar(select(SmsChallenge).where(SmsChallenge.user_id == uid)) is None
            audits = list(
                db.scalars(select(Audit).where(Audit.user_id == uid, Audit.action.like("phone.%")))
            )
            assert {row.action for row in audits} == {"phone.bind.request", "phone.bind.confirm"}
            assert all(
                phone not in str(row.detail) and demo_sender[phone] not in str(row.detail)
                for row in audits
            )
    finally:
        own.close()
        second.close()
        other.close()


def test_binding_sms_purpose_cookie_expiry_attempts_and_credential_version(accounts, demo_sender):
    own, headers = login(accounts)
    outsider, _ = login(accounts)
    phone = number()
    try:
        data = issue(own, headers, phone, accounts[1]).json()
        token = data["challenge_id"]
        assert (
            confirm(outsider, headers, token, demo_sender[phone]).status_code == 403
        )  # different session CSRF
        other_me = outsider.get("/api/v1/auth/me").json()
        assert (
            confirm(
                outsider, {"x-csrf-token": other_me["csrf_token"]}, token, demo_sender[phone]
            ).status_code
            == 422
        )
        with SessionLocal.begin() as db:
            row = db.get(SmsChallenge, token_hash(token))
            row.purpose = "recovery"
        assert confirm(own, headers, token, demo_sender[phone]).status_code == 422
        with SessionLocal.begin() as db:
            row = db.get(SmsChallenge, token_hash(token))
            row.purpose = "bind"
        bad = "000000" if demo_sender[phone] != "000000" else "111111"
        for _ in range(5):
            assert confirm(own, headers, token, bad).status_code == 422
        assert confirm(own, headers, token, demo_sender[phone]).status_code == 422
        with SessionLocal.begin() as db:
            row = db.get(SmsChallenge, token_hash(token))
            assert row.attempts == 5 and row.consumed_at is not None
            row.created_at = now() - timedelta(minutes=2)
        fresh = issue(own, headers, phone, accounts[1]).json()
        token = fresh["challenge_id"]
        with SessionLocal.begin() as db:
            row = db.get(SmsChallenge, token_hash(token))
            row.expires_at = now() - timedelta(seconds=1)
        assert confirm(own, headers, token, demo_sender[phone]).status_code == 422
        with SessionLocal.begin() as db:
            row = db.get(SmsChallenge, token_hash(token))
            row.created_at = now() - timedelta(minutes=2)
        fresh = issue(own, headers, phone, accounts[1]).json()
        token = fresh["challenge_id"]
        with SessionLocal.begin() as db:
            user = db.scalar(select(User).where(User.username == accounts[0][0]))
            user.version += 1
        assert confirm(own, headers, token, demo_sender[phone]).status_code == 422
        assert own.get("/api/v1/auth/phone").json()["verified"] is False
        with SessionLocal.begin() as db:
            db.get(SmsChallenge, token_hash(token)).created_at = now() - timedelta(minutes=2)
        fresh = issue(own, headers, phone, accounts[1]).json()
        changed = own.post(
            "/api/v1/auth/password",
            headers=headers,
            json={
                "current_password": accounts[1],
                "new_password": "Phone-proof-changed-" + secrets.token_hex(8),
            },
        )
        assert changed.status_code == 200, changed.text
        with SessionLocal() as db:
            assert db.get(SmsChallenge, token_hash(fresh["challenge_id"])) is None
    finally:
        own.close()
        outsider.close()


def test_two_accounts_cannot_concurrently_bind_one_phone(accounts, demo_sender):
    a, ah = login(accounts)
    b, bh = login(accounts, 1)
    phone = number()
    try:
        first = issue(a, ah, phone, accounts[1]).json()
        first_code = demo_sender[phone]
        second = issue(b, bh, phone, accounts[1]).json()
        second_code = demo_sender[phone]
        gate = Barrier(2)

        def bind(args):
            client, headers, token, code = args
            gate.wait(timeout=5)
            return confirm(client, headers, token, code).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    bind,
                    [
                        (a, ah, first["challenge_id"], first_code),
                        (b, bh, second["challenge_id"], second_code),
                    ],
                )
            )
        assert sorted(results) == [200, 409]
        with SessionLocal() as db:
            assert (
                len(list(db.scalars(select(VerifiedPhone).where(VerifiedPhone.phone == phone))))
                == 1
            )
    finally:
        a.close()
        b.close()


def test_sms_is_disabled_without_provider_and_demo_never_falls_back_in_production(
    accounts, monkeypatch
):
    own, headers = login(accounts)
    try:
        monkeypatch.setattr(settings, "sms_mode", "disabled")
        assert issue(own, headers, number(), accounts[1]).status_code == 503
        monkeypatch.setattr(settings, "sms_mode", "demo")
        monkeypatch.setattr(settings, "demo_mode", False)
        assert issue(own, headers, number(), accounts[1]).status_code == 503
        with pytest.raises(ValidationError) as error:
            Settings(
                database_url="postgresql://user:settings-secret@unused",
                demo_mode=False,
                sms_mode="demo",
                _env_file=None,
            )
        assert "settings-secret" not in str(error.value)
    finally:
        own.close()
