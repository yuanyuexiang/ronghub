import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import sms
from app.config import settings
from app.db import SessionLocal
from app.dependencies import token_hash
from app.main import app
from app.models import Audit, LoginSession, Operator, SmsChallenge, User, VerifiedPhone, now


@pytest.fixture()
def phone_account(accounts, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "sms_mode", "demo")
    phone = "139" + f"{secrets.randbelow(100000000):08d}"
    sent = {}

    def send(self, destination, code, purpose):
        sent[(purpose, destination)] = code
        return secrets.token_urlsafe(24)

    monkeypatch.setattr(sms.DemoSmsAdapter, "send", send)
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.username == accounts[0][0]))
        uid = user.id
        db.add(VerifiedPhone(user_id=uid, phone=phone, verification_mode="demo"))
    return uid, phone, sent


def client():
    return TestClient(app, client=("sms-auth-" + secrets.token_hex(8), 1))


def issued(c, identifier, purpose="login", kind="account"):
    challenge = c.get("/api/v1/auth/captcha")
    assert challenge.status_code == 200
    return c.post(
        "/api/v1/auth/sms/challenges",
        json={
            "purpose": purpose,
            "identifier_kind": kind,
            "identifier": identifier,
            "captcha_id": challenge.json()["captcha_id"],
            "captcha_answer": "ABCDE",
        },
    )


def submit(c, token, code, purpose="login", password=None):
    body = {"challenge_id": token, "code": code}
    if password is not None:
        body["new_password"] = password
    return c.post(
        "/api/v1/auth/sms/" + ("recover" if purpose == "recovery" else "login"), json=body
    )


def test_issuance_does_not_disclose_eligibility_and_requires_captcha(accounts, phone_account):
    _uid, phone, sent = phone_account
    c = client()
    try:
        assert (
            c.post(
                "/api/v1/auth/sms/challenges",
                json={"purpose": "login", "identifier": accounts[0][0]},
            ).status_code
            == 422
        )
        responses = []
        for name in [accounts[0][0], accounts[0][1], "missing-" + secrets.token_hex(8)]:
            result = issued(c, name)
            assert result.status_code == 201, result.text
            data = result.json()
            responses.append(data)
            assert set(data) == {
                "challenge_id",
                "expires_at",
                "expires_in_seconds",
                "retry_after_seconds",
                "delivery_mode",
                "message",
            }
            assert name not in result.text and phone not in result.text
        assert len({row["message"] for row in responses}) == 1
        assert len(sent) == 1
        with SessionLocal() as db:
            assert db.get(SmsChallenge, token_hash(responses[1]["challenge_id"])).user_id is None
        assert submit(c, responses[1]["challenge_id"], "123456").status_code == 422
        assert issued(c, accounts[0][0]).status_code == 429
        # Independent phone namespace avoids ambiguous numeric usernames.
        by_phone = issued(c, "+86 " + phone, kind="phone")
        assert by_phone.status_code == 201
        assert submit(c, by_phone.json()["challenge_id"], sent[("login", phone)]).status_code == 200
    finally:
        c.close()


def test_sms_login_purpose_binding_and_parallel_single_session(accounts, phone_account):
    uid, phone, sent = phone_account
    c = client()
    other = client()
    peers = []
    try:
        result = issued(c, accounts[0][0])
        assert result.status_code == 201
        token = result.json()["challenge_id"]
        code = sent[("login", phone)]
        assert submit(other, token, code).status_code == 422
        assert submit(c, token, code, "recovery", "Another-secure-password-123").status_code == 422
        for _ in range(2):
            p = client()
            p.cookies.update(c.cookies)
            peers.append(p)
        barrier = Barrier(2)

        def login(p):
            barrier.wait(timeout=5)
            return submit(p, token, code).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(login, peers)) == [200, 422]
        with SessionLocal() as db:
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(LoginSession)
                    .where(LoginSession.user_id == uid)
                )
                == 1
            )
            audit = db.scalar(
                select(Audit).where(Audit.user_id == uid, Audit.action == "auth.sms.login")
            )
            assert audit and phone not in str(audit.detail) and code not in str(audit.detail)
        assert submit(c, token, code).status_code == 422
    finally:
        c.close()
        other.close()
        for p in peers:
            p.close()


def test_recovery_replaces_credentials_revokes_all_sessions_and_other_purposes(
    accounts, phone_account
):
    uid, phone, sent = phone_account
    a, b, c = client(), client(), client()
    try:
        for device in [a, b]:
            assert (
                device.post(
                    "/api/v1/auth/login", json={"username": accounts[0][0], "password": accounts[1]}
                ).status_code
                == 200
            )
        pending = issued(c, accounts[0][0])
        login_token = pending.json()["challenge_id"]
        login_code = sent[("login", phone)]
        with SessionLocal.begin() as db:
            db.get(User, uid).must_change_password = True
        request = issued(c, accounts[0][0], "recovery")
        assert request.status_code == 201
        token = request.json()["challenge_id"]
        code = sent[("recovery", phone)]
        same = submit(c, token, code, "recovery", accounts[1])
        assert same.status_code == 422
        password = "Recovered-" + secrets.token_urlsafe(18)
        result = submit(c, token, code, "recovery", password)
        assert result.status_code == 200, result.text
        assert result.json() == {"ok": True, "reauthenticate": True}
        for device in [a, b, c]:
            assert device.get("/api/v1/auth/me").status_code == 401
        assert submit(c, token, code, "recovery", password + "x").status_code == 422
        assert submit(c, login_token, login_code).status_code == 422
        assert (
            a.post(
                "/api/v1/auth/login", json={"username": accounts[0][0], "password": accounts[1]}
            ).status_code
            == 401
        )
        assert (
            a.post(
                "/api/v1/auth/login", json={"username": accounts[0][0], "password": password}
            ).status_code
            == 200
        )
        with SessionLocal() as db:
            assert db.get(User, uid).must_change_password is False
            assert db.scalar(select(SmsChallenge).where(SmsChallenge.user_id == uid)) is None
            log = db.scalar(
                select(Audit).where(Audit.user_id == uid, Audit.action == "password.recover")
            )
            assert (
                log
                and password not in str(log.detail)
                and code not in str(log.detail)
                and phone not in str(log.detail)
            )
    finally:
        a.close()
        b.close()
        c.close()


def test_sms_auth_rejects_stale_phone_proof_and_account_versions(accounts, phone_account):
    uid, phone, sent = phone_account
    c = client()
    try:
        one = issued(c, accounts[0][0])
        token = one.json()["challenge_id"]
        with SessionLocal.begin() as db:
            db.get(VerifiedPhone, uid).version += 1
        assert submit(c, token, sent[("login", phone)]).status_code == 422
        two = issued(c, phone, kind="phone")
        token = two.json()["challenge_id"]
        with SessionLocal.begin() as db:
            db.get(User, uid).version += 1
        assert submit(c, token, sent[("login", phone)]).status_code == 422
        three = issued(c, accounts[0][0], "recovery")
        token = three.json()["challenge_id"]
        with SessionLocal.begin() as db:
            user = db.get(User, uid)
            db.get(Operator, user.operator_id).enabled = False
        assert (
            submit(
                c, token, sent[("recovery", phone)], "recovery", "Never-applied-password-123"
            ).status_code
            == 422
        )
        with SessionLocal() as db:
            assert db.get(SmsChallenge, token_hash(token)).consumed_at is not None
    finally:
        c.close()


def test_sms_auth_wrong_codes_expiry_and_disabled_provider(accounts, phone_account, monkeypatch):
    _uid, phone, sent = phone_account
    c = client()
    try:
        one = issued(c, accounts[0][0])
        token = one.json()["challenge_id"]
        bad = "000000" if sent[("login", phone)] != "000000" else "111111"
        for _ in range(5):
            assert submit(c, token, bad).status_code == 422
        assert submit(c, token, sent[("login", phone)]).status_code == 422
        two = issued(c, accounts[0][0], "recovery")
        token = two.json()["challenge_id"]
        with SessionLocal.begin() as db:
            db.get(SmsChallenge, token_hash(token)).expires_at = now() - timedelta(seconds=1)
        assert (
            submit(
                c, token, sent[("recovery", phone)], "recovery", "Unused-password-value-123"
            ).status_code
            == 422
        )
        monkeypatch.setattr(settings, "sms_mode", "disabled")
        assert issued(c, phone, kind="phone").status_code == 503
        assert (
            submit(
                c, token, sent[("recovery", phone)], "recovery", "Unused-password-value-123"
            ).status_code
            == 503
        )
        monkeypatch.setattr(settings, "sms_mode", "demo")
        monkeypatch.setattr(settings, "demo_mode", False)
        assert issued(c, phone, kind="phone").status_code == 503
    finally:
        c.close()


def test_numeric_username_and_phone_are_distinct_identifier_namespaces(accounts, phone_account):
    uid, phone, sent = phone_account
    c = client()
    try:
        with SessionLocal.begin() as db:
            other = db.scalar(select(User).where(User.username == accounts[0][1]))
            other.username = phone
        account_result = issued(c, phone, kind="account")
        phone_result = issued(c, phone, kind="phone")
        assert account_result.status_code == phone_result.status_code == 201
        with SessionLocal() as db:
            assert (
                db.get(SmsChallenge, token_hash(account_result.json()["challenge_id"])).user_id
                is None
            )
            assert (
                db.get(SmsChallenge, token_hash(phone_result.json()["challenge_id"])).user_id == uid
            )
        assert (
            submit(c, phone_result.json()["challenge_id"], sent[("login", phone)]).status_code
            == 200
        )
        assert c.get("/api/v1/auth/me").json()["username"] == accounts[0][0]
    finally:
        c.close()


def test_concurrent_recovery_consumes_one_proof_and_commits_one_password(accounts, phone_account):
    uid, phone, sent = phone_account
    c = client()
    peers = []
    try:
        proof = issued(c, accounts[0][0], "recovery").json()
        code = sent[("recovery", phone)]
        for _ in range(2):
            peer = client()
            peer.cookies.update(c.cookies)
            peers.append(peer)
        gate = Barrier(2)
        passwords = [
            "First-recovery-" + secrets.token_urlsafe(16),
            "Second-recovery-" + secrets.token_urlsafe(16),
        ]

        def reset(args):
            peer, password = args
            gate.wait(timeout=5)
            return submit(peer, proof["challenge_id"], code, "recovery", password).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reset, zip(peers, passwords, strict=True)))
        assert sorted(results) == [200, 422]
        from app.dependencies import passwords as hasher

        with SessionLocal() as db:
            user = db.get(User, uid)
            assert [hasher.verify(value, user.password_hash) for value in passwords] == [
                status == 200 for status in results
            ]
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(Audit)
                    .where(Audit.user_id == uid, Audit.action == "password.recover")
                )
                == 1
            )
            assert db.scalar(select(SmsChallenge).where(SmsChallenge.user_id == uid)) is None
    finally:
        c.close()
        for peer in peers:
            peer.close()
