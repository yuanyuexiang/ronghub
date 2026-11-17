import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from test_passwords import linked_registration as linked_registration  # noqa: PLC0414

from app.config import settings
from app.contracts import ContentReplyCreated, PasswordChanged, PasswordResetResult
from app.db import SessionLocal
from app.dependencies import token_hash
from app.main import app
from app.models import (
    Article,
    ArticleAction,
    ArticleReply,
    ArticleReplyAttachment,
    Audit,
    EnrollmentSession,
    LoginSession,
    LoginThrottle,
    NoticeDelivery,
    Registration,
    User,
)


def client_for(name, password):
    client = TestClient(
        app, client=("identity-reply-" + secrets.token_hex(8), 1), raise_server_exceptions=False
    )
    response = client.post("/api/v1/auth/login", json={"username": name, "password": password})
    assert response.status_code == 200
    return client, {"x-csrf-token": response.json()["csrf_token"]}


def fail(value):
    raise RuntimeError("Reject projection before credential or private evidence commit")


@pytest.mark.parametrize("operation", ["change", "reset"])
def test_password_projection_failure_keeps_credentials_sessions_and_enrollment(
    accounts, staff, linked_registration, monkeypatch, operation
):
    names, password, _ = accounts
    owner, oh = client_for(names[0], password)
    second, _ = client_for(names[0], password)
    reg, rh = client_for(staff[0][0], password)
    try:
        with SessionLocal() as db:
            target = db.scalar(select(User).where(User.username == names[0]))
            uid, before_hash, version = target.id, target.password_hash, target.version
            enrollment_hash = db.get(Registration, linked_registration[0]).password_hash
            actor_id = (
                uid
                if operation == "change"
                else db.scalar(select(User.id).where(User.username == staff[0][0]))
            )
        replacement = secrets.token_urlsafe(24)
        if operation == "change":
            actor, headers, model = owner, oh, PasswordChanged
            path = "/api/v1/auth/password"
            body = {"current_password": password, "new_password": replacement}
        else:
            actor, headers, model = reg, rh, PasswordResetResult
            path = f"/api/v1/accounts/{uid}/password-reset"
            body = {
                "admin_password": password,
                "temporary_password": replacement,
                "version": version,
                "reason": "Verified fixture owner",
            }
        with monkeypatch.context() as patch:
            patch.setattr(model, "model_validate", fail)
            response = actor.post(path, headers=headers, json=body)
            assert response.status_code == 500
            assert "ronghub_session=" not in response.headers.get("set-cookie", "")
        with SessionLocal() as db:
            target = db.get(User, uid)
            assert (target.password_hash, target.version, target.must_change_password) == (
                before_hash,
                version,
                False,
            )
            assert db.get(Registration, linked_registration[0]).password_hash == enrollment_hash
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(LoginSession)
                    .where(LoginSession.user_id == uid)
                )
                == 2
            )
            assert (
                db.scalar(
                    select(EnrollmentSession.token_hash).where(
                        EnrollmentSession.registration_id == linked_registration[0]
                    )
                )
                is not None
            )
            assert (
                db.scalar(
                    select(Audit.id).where(
                        Audit.object_id == uid, Audit.action == "password." + operation
                    )
                )
                is None
            )
            # Verification attempts intentionally commit before business locks;
            # response failure must not erase the anti-bruteforce counter.
            throttle = db.get(LoginThrottle, token_hash(f"password-action:{operation}:{actor_id}"))
            assert throttle.attempts == 1
        assert owner.get("/api/v1/auth/me").status_code == 200
        assert second.get("/api/v1/auth/me").status_code == 200
        success = actor.post(path, headers=headers, json=body)
        assert success.status_code == 200 and success.headers["cache-control"] == "no-store"
        assert replacement not in success.text and password not in success.text
        assert second.get("/api/v1/auth/me").status_code == 401
    finally:
        owner.close()
        second.close()
        reg.close()


def test_image_reply_projection_rolls_back_rows_and_files_then_serves_private_binary(
    accounts, staff, monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    clients = [client_for(name, accounts[1]) for name in [staff[0][0], *accounts[0]]]
    aid = None
    try:
        (reg, rh), (owner, oh), (other, _) = clients
        oid = owner.get("/api/v1/auth/me").json()["operator_id"]
        created = reg.post(
            "/api/v1/content",
            headers=rh,
            json={
                "kind": "notice",
                "title": "Private reply contract",
                "body": "Fixture",
                "audience": "operator",
                "recipient_ids": [oid],
            },
        )
        assert created.status_code == 201
        aid = created.json()["id"]
        path = f"/api/v1/content/{aid}"
        assert (
            reg.post(
                path + "/actions", headers=rh, json={"action": "publish", "version": 1}
            ).status_code
            == 200
        )
        png = b"\x89PNG\r\n\x1a\nprivate-evidence"

        def send():
            return owner.post(
                path + "/replies/with-images",
                headers=oh,
                data={"body": "Evidence preserved together"},
                files={"files": ("evidence.png", png, "image/png")},
            )

        with monkeypatch.context() as patch:
            patch.setattr(ContentReplyCreated, "model_validate", fail)
            assert send().status_code == 500
        assert not list(tmp_path.iterdir())
        with SessionLocal() as db:
            assert db.scalar(select(ArticleReply.id).where(ArticleReply.article_id == aid)) is None
        success = send()
        assert success.status_code == 201 and success.headers["cache-control"] == "no-store"
        assert set(success.json()) == {"id"}
        reply = owner.get(path).json()["replies"][0]
        assert reply["id"] == success.json()["id"]
        image_path = path + f"/replies/{reply['id']}/images/{reply['images'][0]['id']}"
        for client, query in [(owner, ""), (reg, "?manage=true")]:
            result = client.get(image_path + query)
            assert result.status_code == 200 and result.content == png
            assert result.headers["content-type"] == "image/png"
            assert result.headers["cache-control"] == "no-store"
            assert result.headers["x-content-type-options"] == "nosniff"
        assert other.get(image_path).status_code == 404
        media = app.openapi()["paths"][
            "/api/v1/content/{article_id}/replies/{reply_id}/images/{image_id}"
        ]["get"]["responses"]["200"]["content"]
        assert set(media) == {"image/png", "image/jpeg"}
        assert all(
            shape["schema"] == {"type": "string", "format": "binary"} for shape in media.values()
        )
    finally:
        for client, _ in clients:
            client.close()
        if aid is not None:
            with SessionLocal.begin() as db:
                replies = select(ArticleReply.id).where(ArticleReply.article_id == aid)
                db.execute(
                    delete(ArticleReplyAttachment).where(
                        ArticleReplyAttachment.reply_id.in_(replies)
                    )
                )
                db.execute(delete(ArticleReply).where(ArticleReply.article_id == aid))
                db.execute(delete(NoticeDelivery).where(NoticeDelivery.article_id == aid))
                db.execute(delete(ArticleAction).where(ArticleAction.article_id == aid))
                db.execute(delete(Article).where(Article.id == aid))
