from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import select
from test_phone_binding import demo_sender as demo_sender  # noqa: PLC0414
from test_phone_binding import login, number

from app import sms
from app.config import settings
from app.db import SessionLocal
from app.dependencies import token_hash
from app.models import Audit, SmsChallenge, User, VerifiedPhone, now

BASE = "/api/v1/auth/phone/change"


@pytest.fixture()
def bound(accounts):
    old = number()
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.username == accounts[0][0]))
        uid = user.id
        db.add(VerifiedPhone(user_id=uid, phone=old, verification_mode="demo"))
    return uid, old


def start(client, headers, password, version=1):
    return client.post(
        BASE + "/challenges",
        headers=headers,
        json={"current_password": password, "phone_version": version},
    )


def next_step(client, headers, token, code, phone):
    return client.post(
        BASE + "/new-challenges",
        headers=headers,
        json={"old_challenge_id": token, "old_code": code, "phone": phone},
    )


def finish(client, headers, token, code):
    return client.post(
        BASE + "/confirm", headers=headers, json={"challenge_id": token, "code": code}
    )


def exchange(client, headers, password, old, new, captured):
    first = start(client, headers, password)
    assert first.status_code == 201, first.text
    second = next_step(client, headers, first.json()["challenge_id"], captured[old], "+86 " + new)
    assert second.status_code == 201, second.text
    return first.json()["challenge_id"], second.json()["challenge_id"], captured[new]


def test_two_phone_proofs_replace_identity_and_revoke_every_session(bound, accounts, demo_sender):
    client, headers = login(accounts)
    other, _ = login(accounts)
    uid, old = bound
    new = number()
    try:
        _old_token, token, code = exchange(client, headers, accounts[1], old, new, demo_sender)
        with SessionLocal() as db:
            assert db.get(VerifiedPhone, uid).phone == old
        result = finish(client, headers, token, code)
        assert result.status_code == 200, result.text
        assert result.json()["phone_masked"] == sms.mask(new)
        assert result.headers["cache-control"] == "no-store"
        assert client.get("/api/v1/auth/me").status_code == 401
        assert other.get("/api/v1/auth/me").status_code == 401
        with SessionLocal() as db:
            phone = db.get(VerifiedPhone, uid)
            assert phone.phone == new and phone.version == 2
            assert db.get(User, uid).version == 2
            assert db.scalar(select(SmsChallenge).where(SmsChallenge.user_id == uid)) is None
            audits = list(
                db.scalars(
                    select(Audit).where(Audit.user_id == uid, Audit.action.like("phone.change.%"))
                )
            )
            assert len(audits) == 3
            assert all(
                old not in str(a.detail) and new not in str(a.detail) and code not in str(a.detail)
                for a in audits
            )
    finally:
        client.close()
        other.close()


def test_password_version_csrf_purpose_browser_and_old_proof_cannot_be_skipped(
    bound, accounts, demo_sender
):
    own, headers = login(accounts)
    other, other_headers = login(accounts)
    try:
        assert start(own, {}, accounts[1]).status_code == 403
        assert start(own, headers, "wrong").status_code == 403
        assert start(own, headers, accounts[1], version=2).status_code == 409
        issued = start(own, headers, accounts[1])
        assert issued.status_code == 201
        token = issued.json()["challenge_id"]
        assert start(own, headers, accounts[1]).status_code == 429
        assert finish(own, headers, token, demo_sender[bound[1]]).status_code == 422
        assert (
            next_step(other, other_headers, token, demo_sender[bound[1]], number()).status_code
            == 422
        )
        same = next_step(own, headers, token, demo_sender[bound[1]], bound[1])
        assert same.status_code == 422
        occupied = number()
        with SessionLocal.begin() as db:
            uid2 = db.scalar(select(User.id).where(User.username == accounts[0][1]))
            db.add(VerifiedPhone(user_id=uid2, phone=occupied, verification_mode="demo"))
        wrong = "000000" if demo_sender[bound[1]] != "000000" else "111111"
        # Occupied-number information is not checked before verifying the old phone.
        assert next_step(own, headers, token, wrong, occupied).status_code == 422
        assert next_step(own, headers, token, demo_sender[bound[1]], occupied).status_code == 409
        second = next_step(own, headers, token, demo_sender[bound[1]], number())
        assert second.status_code == 201, second.text
        assert next_step(own, headers, token, demo_sender[bound[1]], number()).status_code == 422
    finally:
        own.close()
        other.close()


def test_expired_parent_and_stale_binding_block_final_change(bound, accounts, demo_sender):
    client, headers = login(accounts)
    try:
        new = number()
        old_token, token, code = exchange(client, headers, accounts[1], bound[1], new, demo_sender)
        with SessionLocal.begin() as db:
            db.get(SmsChallenge, token_hash(old_token)).expires_at = now() - timedelta(seconds=1)
        assert finish(client, headers, token, code).status_code == 422
        with SessionLocal.begin() as db:
            db.get(SmsChallenge, token_hash(old_token)).expires_at = now() + timedelta(minutes=1)
            db.get(VerifiedPhone, bound[0]).version = 2
        assert finish(client, headers, token, code).status_code == 409
        with SessionLocal() as db:
            assert db.get(VerifiedPhone, bound[0]).phone == bound[1]
    finally:
        client.close()


def test_restart_invalidates_entire_previous_exchange(bound, accounts, demo_sender):
    client, headers = login(accounts)
    try:
        new = number()
        _, token, code = exchange(client, headers, accounts[1], bound[1], new, demo_sender)
        with SessionLocal.begin() as db:
            parent = db.scalar(
                select(SmsChallenge).where(
                    SmsChallenge.user_id == bound[0], SmsChallenge.purpose == "phone_change_old"
                )
            )
            parent.created_at = now() - timedelta(seconds=61)
        assert start(client, headers, accounts[1]).status_code == 201
        assert finish(client, headers, token, code).status_code == 422
    finally:
        client.close()


def test_concurrent_confirmation_changes_once_and_requires_reauthentication(
    bound, accounts, demo_sender
):
    first, h1 = login(accounts)
    second, h2 = login(accounts)
    try:
        _, token, code = exchange(first, h1, accounts[1], bound[1], number(), demo_sender)
        second.cookies.set(sms.COOKIE, first.cookies.get(sms.COOKIE))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda value: finish(value[0], value[1], token, code).status_code,
                    [(first, h1), (second, h2)],
                )
            )
        assert sorted(results) == [200, 401]
        with SessionLocal() as db:
            assert db.get(VerifiedPhone, bound[0]).version == 2
            assert (
                len(
                    list(
                        db.scalars(
                            select(Audit).where(
                                Audit.user_id == bound[0], Audit.action == "phone.change.confirm"
                            )
                        )
                    )
                )
                == 1
            )
    finally:
        first.close()
        second.close()


def test_disabled_provider_does_not_start_or_replace_phone(
    bound, accounts, demo_sender, monkeypatch
):
    monkeypatch.setattr(settings, "sms_mode", "disabled")
    client, headers = login(accounts)
    try:
        assert start(client, headers, accounts[1]).status_code == 503
        assert demo_sender == {}
        with SessionLocal() as db:
            assert db.get(VerifiedPhone, bound[0]).phone == bound[1]
    finally:
        client.close()


def test_new_number_race_preserves_losing_binding_and_session(bound, accounts, demo_sender):
    old2 = number()
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.username == accounts[0][1]))
        uid2 = user.id
        db.add(VerifiedPhone(user_id=uid2, phone=old2, verification_mode="demo"))
    first, h1 = login(accounts)
    second, h2 = login(accounts, 1)
    shared = number()
    try:
        _, t1, c1 = exchange(first, h1, accounts[1], bound[1], shared, demo_sender)
        _, t2, c2 = exchange(second, h2, accounts[1], old2, shared, demo_sender)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda args: finish(*args).status_code,
                    [(first, h1, t1, c1), (second, h2, t2, c2)],
                )
            )
        assert sorted(results) == [200, 409]
        with SessionLocal() as db:
            phones = [db.get(VerifiedPhone, uid) for uid in [bound[0], uid2]]
            assert sum(p.phone == shared for p in phones) == 1
            assert sorted(p.version for p in phones) == [1, 2]
        loser = first if results[0] == 409 else second
        assert loser.get("/api/v1/auth/me").status_code == 200
    finally:
        first.close()
        second.close()


def test_exhausted_new_code_and_projection_failure_never_replace_phone(
    bound, accounts, demo_sender, monkeypatch
):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import phone_change

    client, headers = login(accounts)
    try:
        new = number()
        _, token, code = exchange(client, headers, accounts[1], bound[1], new, demo_sender)

        def fail(_):
            raise RuntimeError("projection proof")

        original = phone_change.PhoneBound.model_validate
        monkeypatch.setattr(phone_change.PhoneBound, "model_validate", fail)
        with TestClient(app, raise_server_exceptions=False) as probe:
            probe.cookies.update(client.cookies)
            result = finish(probe, headers, token, code)
            assert result.status_code == 500
        monkeypatch.setattr(phone_change.PhoneBound, "model_validate", original)
        with SessionLocal() as db:
            assert db.get(VerifiedPhone, bound[0]).phone == bound[1]
            assert db.get(SmsChallenge, token_hash(token)).consumed_at is None
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            assert finish(client, headers, token, wrong).status_code == 422
        assert finish(client, headers, token, code).status_code == 422
        with SessionLocal() as db:
            assert db.get(VerifiedPhone, bound[0]).version == 1
    finally:
        client.close()
