import secrets
from datetime import timedelta

import pytest
import test_protocols as publication
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.authorization import normalize_grants
from app.db import SessionLocal
from app.main import app
from app.models import Article, RoleProfile, User, now

base_case = publication.base_case
case = publication.case


@pytest.fixture()
def anonymous():
    with TestClient(app, client=("anonymous-" + secrets.token_hex(8), 1)) as client:
        yield client


def upload(c, item):
    response = c["clients"][0].post(
        f"/api/v1/content/{item['id']}/attachments",
        headers=c["headers"][0],
        data={"version": item["version"]},
        files={"file": ("public.pdf", b"%PDF-1.4 Explicit public proof", "application/pdf")},
    )
    assert response.status_code == 201
    return response.json()["article"], response.json()["attachments"][0]


def test_authenticated_all_is_not_public_and_login_does_not_override_public_gate(case, anonymous):
    c = case
    item = publication.create(c, kind="announcement")
    item, material = upload(c, item)
    item = publication.publish(c, item)
    assert item["portal_public"] is False
    assert c["clients"][1].get(f"/api/v1/content/{item['id']}").status_code == 200
    for client in [anonymous, c["clients"][1]]:
        assert client.get("/api/v1/public/articles", params={"q": c["prefix"]}).json()["total"] == 0
        assert client.get(f"/api/v1/public/articles/{item['id']}").status_code == 404
        assert (
            client.get(
                f"/api/v1/public/articles/{item['id']}/attachments/{material['id']}"
            ).status_code
            == 404
        )


def test_explicit_publication_anonymous_projection_frozen_file_and_withdrawal(case, anonymous):
    c = case
    item = publication.create(c, kind="news", portal_public=True)
    item, material = upload(c, item)
    path = f"/api/v1/public/articles/{item['id']}"
    assert anonymous.get(path).status_code == 404
    item = publication.publish(c, item)
    assert anonymous.get("/api/v1/auth/me").status_code == 401
    response = anonymous.get(path)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    detail = response.json()
    assert detail["title"] == c["prefix"] and len(detail["attachments"]) == 1
    assert not {
        "operator_id",
        "recipient_ids",
        "created_by",
        "history",
        "audience",
        "portal_public",
        "version",
    }.intersection(detail)
    assert "storage_key" not in response.text
    download = path + f"/attachments/{material['id']}"
    file = anonymous.get(download)
    assert file.status_code == 200 and file.content == b"%PDF-1.4 Explicit public proof"
    assert (
        file.headers["cache-control"] == "no-store"
        and file.headers["content-security-policy"] == "sandbox"
    )
    private = publication.create(c, kind="news")
    private, other = upload(c, private)
    assert anonymous.get(path + f"/attachments/{other['id']}").status_code == 404
    withdrawn = c["clients"][0].post(
        f"/api/v1/content/{item['id']}/actions",
        headers=c["headers"][0],
        json={"version": item["version"], "action": "withdraw"},
    )
    assert withdrawn.status_code == 200
    assert anonymous.get(path).status_code == anonymous.get(download).status_code == 404


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "notice"},
        {"kind": "protocol"},
        {"audience": "operator"},
        {"audience": "regulator"},
        {"operator_id": 1},
        {"recipient_ids": [1]},
    ],
)
def test_public_flag_rejects_private_kind_or_target_before_persisting(case, fields):
    c = case
    response = c["clients"][0].post(
        "/api/v1/content",
        headers=c["headers"][0],
        json={
            "kind": "news",
            "title": c["prefix"],
            "body": "Not eligible for public portal",
            "portal_public": True,
            **fields,
        },
    )
    assert response.status_code == 422
    with SessionLocal() as db:
        assert db.scalar(select(Article.id).where(Article.title == c["prefix"])) is None


def test_scheduled_publication_and_explicit_contact_are_independent_of_login(case, anonymous):
    c = case
    item = publication.publish(
        c,
        publication.create(c, kind="announcement", portal_public=True),
        published_at=(now() + timedelta(days=1)).isoformat(),
    )
    path = f"/api/v1/public/articles/{item['id']}"
    assert anonymous.get(path).status_code == 404
    with SessionLocal.begin() as db:
        db.get(Article, item["id"]).published_at = now() - timedelta(seconds=1)
    assert anonymous.get(path).status_code == 200
    contact = publication.publish(
        c, publication.create(c, kind="contact", portal_public=True, title=c["prefix"] + " contact")
    )
    assert anonymous.get("/api/v1/public/articles", params={"q": c["prefix"]}).json()["total"] == 1
    contacts = anonymous.get(
        "/api/v1/public/articles", params={"q": c["prefix"], "kind": "contact"}
    ).json()
    assert contacts["total"] == 1 and contacts["items"][0]["id"] == contact["id"]
    assert anonymous.get("/api/v1/public/articles", params={"kind": "protocol"}).status_code == 422


def test_legacy_content_write_and_old_publisher_do_not_gain_public_consent(case):
    c = case
    assert "content.public" not in normalize_grants("regulator", ["content.read", "content.write"])
    private = publication.create(c, kind="announcement")
    public = publication.create(c, kind="news", portal_public=True, title=c["prefix"] + " public")
    with SessionLocal.begin() as db:
        uid = db.get(Article, private["id"]).created_by
        user = db.get(User, uid)
        previous = user.role_profile_id
        profile = RoleProfile(
            name=c["prefix"],
            base_role="regulator",
            permissions=[
                "stations.read",
                "content.read",
                "content.manage",
                "content.create",
                "content.edit",
                "content.publish",
            ],
        )
        db.add(profile)
        db.flush()
        pid = profile.id
        user.role_profile_id = pid
    try:
        assert (
            c["clients"][0]
            .post(
                "/api/v1/content",
                headers=c["headers"][0],
                json={
                    "kind": "news",
                    "title": "No public permission",
                    "body": "Denied",
                    "portal_public": True,
                },
            )
            .status_code
            == 403
        )
        assert (
            c["clients"][0]
            .post(
                f"/api/v1/content/{public['id']}/actions",
                headers=c["headers"][0],
                json={"action": "publish", "version": public["version"]},
            )
            .status_code
            == 403
        )
        batch = c["clients"][0].post(
            "/api/v1/content/batch-actions",
            headers=c["headers"][0],
            json={
                "action": "publish",
                "items": [
                    {"id": private["id"], "version": private["version"]},
                    {"id": public["id"], "version": public["version"]},
                ],
            },
        )
        assert batch.status_code == 403
        with SessionLocal() as db:
            assert (
                db.get(Article, private["id"]).status
                == db.get(Article, public["id"]).status
                == "draft"
            )
            assert db.get(Article, private["id"]).version == private["version"]
    finally:
        with SessionLocal.begin() as db:
            db.get(User, uid).role_profile_id = previous
            db.flush()
            db.delete(db.get(RoleProfile, pid))


def test_public_frozen_download_checks_integrity_and_only_exposes_read_routes(case, anonymous):
    from pathlib import Path

    from app.config import settings
    from app.models import ArticleAttachment

    c = case
    item = publication.create(c, kind="news", portal_public=True)
    item, material = upload(c, item)
    publication.publish(c, item)
    with SessionLocal() as db:
        asset = db.get(ArticleAttachment, material["id"])
        filename = Path(settings.storage_path) / asset.storage_key
    filename.write_bytes(b"tampered")
    url = f"/api/v1/public/articles/{item['id']}/attachments/{material['id']}"
    response = anonymous.get(url)
    assert response.status_code == 409 and "json" in response.headers["content-type"]
    assert (
        anonymous.post(
            "/api/v1/public/articles", json={"title": "Anonymous cannot publish"}
        ).status_code
        == 405
    )


def test_public_visibility_count_and_rows_use_one_snapshot(case, anonymous):
    from sqlalchemy import event

    from app.db import engine

    c = case
    item = publication.publish(c, publication.create(c, kind="news", portal_public=True))
    changed = False

    def withdraw(connection, cursor, statement, parameters, context, executemany):
        nonlocal changed
        if changed or "count(" not in statement.lower() or "articles" not in statement.lower():
            return
        changed = True
        with SessionLocal.begin() as db:
            db.get(Article, item["id"]).status = "withdrawn"

    event.listen(engine, "after_cursor_execute", withdraw)
    try:
        response = anonymous.get("/api/v1/public/articles", params={"q": c["prefix"]})
    finally:
        event.remove(engine, "after_cursor_execute", withdraw)
    assert changed and response.status_code == 200
    assert response.json()["total"] == len(response.json()["items"]) == 1
    assert anonymous.get("/api/v1/public/articles", params={"q": c["prefix"]}).json()["total"] == 0
