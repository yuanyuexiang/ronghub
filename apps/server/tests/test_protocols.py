import secrets
from datetime import timedelta

import pytest
import test_inspection_contracts as inspection_cases
from sqlalchemy import delete, event, select

from app.contracts import ContentArticle
from app.db import SessionLocal, engine
from app.models import Article, ArticleAction, ArticleAttachment, Audit, RoleProfile, User, now

base_case = inspection_cases.case


@pytest.fixture()
def case(base_case):
    c = base_case
    c["article_ids"] = []
    c["prefix"] = "protocol-" + secrets.token_hex(6)
    try:
        yield c
    finally:
        with SessionLocal.begin() as db:
            ids = c["article_ids"]
            db.execute(
                delete(Audit).where(Audit.action.like("content.%"), Audit.object_id.in_(ids))
            )
            db.execute(delete(ArticleAttachment).where(ArticleAttachment.article_id.in_(ids)))
            db.execute(delete(ArticleAction).where(ArticleAction.article_id.in_(ids)))
            db.execute(delete(Article).where(Article.id.in_(ids)))


def create(c, **fields):
    body = {
        "kind": "protocol",
        "title": c["prefix"],
        "body": "Synthetic customer protocol, not an official document",
        "audience": "all",
        **fields,
    }
    response = c["clients"][0].post("/api/v1/content", headers=c["headers"][0], json=body)
    assert response.status_code == 201, response.text
    item = response.json()
    c["article_ids"].append(item["id"])
    return item


def publish(c, item, **fields):
    response = c["clients"][0].post(
        f"/api/v1/content/{item['id']}/actions",
        headers=c["headers"][0],
        json={"version": item["version"], "action": "publish", **fields},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_protocol_draft_publish_frozen_download_and_withdrawal_share_visibility(case):
    c = case
    item = create(c)
    path = f"/api/v1/protocols/{item['id']}"
    for actor in c["clients"]:
        assert actor.get(path).status_code == 404
    uploaded = c["clients"][0].post(
        f"/api/v1/content/{item['id']}/attachments",
        headers=c["headers"][0],
        data={"version": item["version"]},
        files={
            "file": (
                "customer-protocol.pdf",
                b"%PDF-1.4 Synthetic customer protocol",
                "application/pdf",
            )
        },
    )
    assert uploaded.status_code == 201
    material = uploaded.json()["attachments"][0]
    item = publish(c, uploaded.json()["article"])
    for actor in c["clients"]:
        response = actor.get(path)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        detail = response.json()
        assert detail["attachment_count"] == 1 and detail["attachments"][0]["frozen"]
        assert detail["body"].startswith("Synthetic customer protocol")
        assert "storage_key" not in response.text and "contact" not in detail
        download = actor.get(f"/api/v1/content/{item['id']}/attachments/{material['id']}")
        assert (
            download.status_code == 200
            and download.content == b"%PDF-1.4 Synthetic customer protocol"
        )
        assert download.headers["cache-control"] == "no-store"
    assert c["clients"][1].post(
        f"/api/v1/content/{item['id']}/replies",
        headers=c["headers"][1],
        json={"body": "Must not become a notice reply"},
    ).status_code in {403, 409}
    removed = c["clients"][0].post(
        f"/api/v1/content/{item['id']}/actions",
        headers=c["headers"][0],
        json={"version": item["version"], "action": "withdraw"},
    )
    assert removed.status_code == 200
    assert c["clients"][1].get(path).status_code == 404
    assert (
        c["clients"][1]
        .get(f"/api/v1/content/{item['id']}/attachments/{material['id']}")
        .status_code
        == 404
    )


def test_target_scheduled_and_unrelated_content_never_becomes_protocol(case):
    c = case
    item = create(c, audience="operator", operator_id=c["task"]["operator_id"])
    publish(c, item, published_at=(now() + timedelta(days=1)).isoformat())
    params = {"q": c["prefix"]}
    assert c["clients"][1].get("/api/v1/protocols", params=params).json()["total"] == 0
    with SessionLocal.begin() as db:
        db.get(Article, item["id"]).published_at = now() - timedelta(seconds=1)
    assert c["clients"][1].get("/api/v1/protocols", params=params).json()["total"] == 1
    for actor in [0, 2, 3]:
        assert c["clients"][actor].get(f"/api/v1/protocols/{item['id']}").status_code == 404
    news = publish(c, create(c, kind="news"))
    assert c["clients"][1].get("/api/v1/protocols", params=params).json()["total"] == 1
    assert c["clients"][1].get(f"/api/v1/protocols/{news['id']}").status_code == 404


def test_reader_grant_does_not_grant_management_or_reveal_drafts(case):
    c = case
    item = publish(c, create(c))
    with SessionLocal.begin() as db:
        uid = db.scalar(select(Article.created_by).where(Article.id == item["id"]))
        user = db.get(User, uid)
        previous = user.role_profile_id
        profile = RoleProfile(
            name=c["prefix"], base_role="regulator", permissions=["stations.read", "content.read"]
        )
        db.add(profile)
        db.flush()
        pid = profile.id
        user.role_profile_id = pid
    try:
        assert c["clients"][0].get(f"/api/v1/protocols/{item['id']}").status_code == 200
        assert c["clients"][0].get("/api/v1/content?manage=true").status_code == 403
        assert (
            c["clients"][0]
            .post(
                "/api/v1/content",
                headers=c["headers"][0],
                json={"kind": "protocol", "title": "Forbidden", "body": "No management"},
            )
            .status_code
            == 403
        )
        with SessionLocal.begin() as db:
            db.get(RoleProfile, pid).permissions = ["stations.read"]
        assert c["clients"][0].get("/api/v1/protocols").status_code == 403
    finally:
        with SessionLocal.begin() as db:
            db.get(User, uid).role_profile_id = previous
            db.flush()
            db.delete(db.get(RoleProfile, pid))


def test_protocol_creation_projection_failure_keeps_article_history_and_audit_atomic(
    case, monkeypatch
):
    c = case
    title = "failed-" + c["prefix"]
    with monkeypatch.context() as patch:
        patch.setattr(ContentArticle, "model_validate", inspection_cases.fail)
        response = c["clients"][0].post(
            "/api/v1/content",
            headers=c["headers"][0],
            json={"kind": "protocol", "title": title, "body": "Must roll back"},
        )
        assert response.status_code == 500
    with SessionLocal() as db:
        assert db.scalar(select(Article.id).where(Article.title == title)) is None
        assert (
            db.scalar(
                select(ArticleAction.id).where(ArticleAction.snapshot["title"].as_string() == title)
            )
            is None
        )


def test_protocol_count_and_rows_share_snapshot_when_publication_changes(case):
    c = case
    item = publish(c, create(c))
    changed = False

    def withdraw(connection, cursor, statement, parameters, context, executemany):
        nonlocal changed
        if changed or "count(" not in statement.lower() or "articles" not in statement.lower():
            return
        changed = True
        with SessionLocal.begin() as writer:
            writer.get(Article, item["id"]).status = "withdrawn"

    event.listen(engine, "after_cursor_execute", withdraw)
    try:
        result = c["clients"][1].get("/api/v1/protocols", params={"q": c["prefix"]})
    finally:
        event.remove(engine, "after_cursor_execute", withdraw)
    assert changed and result.status_code == 200
    assert result.json()["total"] == 1 and len(result.json()["items"]) == 1
    assert c["clients"][1].get("/api/v1/protocols", params={"q": c["prefix"]}).json()["total"] == 0


def test_protocol_pagination_counts_only_visible_protocol_metadata(case):
    c = case
    for index in range(21):
        publish(c, create(c, title=c["prefix"] + "-" + str(index)))
    publish(c, create(c, kind="news", title=c["prefix"] + "-unrelated"))
    page = c["clients"][1].get("/api/v1/protocols", params={"q": c["prefix"], "page": 2})
    assert page.status_code == 200
    result = page.json()
    assert result["page"] == 2 and result["page_size"] == 20 and result["total"] == 21
    assert len(result["items"]) == 1 and result["items"][0]["attachment_count"] == 0
    assert "body" not in result["items"][0] and "rich_document" not in result["items"][0]
    assert c["clients"][1].get("/api/v1/protocols", params={"page": 0}).status_code == 422
