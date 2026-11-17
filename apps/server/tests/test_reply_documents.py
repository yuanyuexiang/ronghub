import json
import secrets
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select
from test_rich_content import doc, image, login, paragraph

from app.config import settings
from app.contracts import ContentReplyCreated
from app.db import SessionLocal
from app.models import Article, ArticleAction, ArticleReply, ArticleReplyAttachment
from app.richtext import RichDocument

PNG = b"\x89PNG\r\n\x1a\nreply-document-fixture"


@pytest.fixture()
def notice(accounts, staff, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    clients = [login(name, accounts[1]) for name in [staff[0][0], *accounts[0], staff[0][1]]]
    (reg, rh), (owner, _), _, _ = clients
    response = reg.post(
        "/api/v1/content",
        headers=rh,
        json={
            "kind": "notice",
            "title": "reply-document-" + secrets.token_hex(6),
            "body": "shared notice",
            "audience": "operator",
        },
    )
    assert response.status_code == 201
    aid = response.json()["id"]
    base = f"/api/v1/content/{aid}"
    try:
        assert (
            reg.post(
                base + "/actions", headers=rh, json={"action": "publish", "version": 1}
            ).status_code
            == 200
        )
        assert owner.get(base).status_code == 200
        yield base, clients, tmp_path
    finally:
        with SessionLocal.begin() as db:
            replies = select(ArticleReply.id).where(ArticleReply.article_id == aid)
            db.execute(
                delete(ArticleReplyAttachment).where(ArticleReplyAttachment.reply_id.in_(replies))
            )
            db.execute(delete(ArticleReply).where(ArticleReply.article_id == aid))
            db.execute(delete(ArticleAction).where(ArticleAction.article_id == aid))
            db.execute(delete(Article).where(Article.id == aid))
        for client, _ in clients:
            client.close()


def formatted():
    value = paragraph("已完成整改 <script>literal</script>")
    value["content"][0]["marks"] = [
        {"type": "bold"},
        {"type": "link", "attrs": {"href": "https://example.com/proof"}},
    ]
    return doc(value)


def test_rich_reply_keeps_format_and_legacy_text_without_trusting_cached_body(notice):
    base, clients, _ = notice
    (reg, _), (owner, headers), (other, _), (association, _) = clients
    response = owner.post(
        base + "/replies",
        headers=headers,
        json={"body": "stale cache", "rich_document": formatted()},
    )
    assert response.status_code == 201
    assert (
        owner.post(base + "/replies", headers=headers, json={"body": "<b>legacy</b>"}).status_code
        == 201
    )
    replies = reg.get(base + "?manage=true").json()["replies"]
    assert (
        RichDocument.model_validate(replies[0]["rich_document"]).model_dump(exclude_none=True)
        == formatted()
    )
    assert replies[0]["body"] == "已完成整改 <script>literal</script>"
    assert replies[1]["rich_document"] is None and replies[1]["body"] == "<b>legacy</b>"
    assert other.get(base).json()["replies"] == []
    assert association.get(base).status_code == 404
    for document in [
        doc(paragraph("text"), image(1)),
        doc(paragraph("x" * 4001)),
        doc(
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": "bad",
                        "marks": [{"type": "link", "attrs": {"href": "javascript:alert(1)"}}],
                    }
                ],
            }
        ),
    ]:
        assert (
            owner.post(
                base + "/replies", headers=headers, json={"rich_document": document}
            ).status_code
            == 422
        )
    assert len(owner.get(base).json()["replies"]) == 2


def test_inline_images_bind_only_this_submission_and_remain_company_private(notice):
    base, clients, _ = notice
    (reg, _), (owner, headers), (other, _), (association, _) = clients
    incoming = doc(paragraph("整改说明"), image(2), image(1), paragraph("检查结束"), image(2))
    result = owner.post(
        base + "/replies/with-images",
        headers=headers,
        data={"body": "ignored", "rich_document": json.dumps(incoming)},
        files=[
            ("files", ("first.png", PNG, "image/png")),
            ("files", ("second.png", PNG, "image/png")),
        ],
    )
    assert result.status_code == 201
    reply = owner.get(base).json()["replies"][0]
    images = reply["images"]
    ids = [
        node["attrs"]["attachment_id"]
        for node in reply["rich_document"]["content"]
        if node["type"] == "privateImage"
    ]
    assert ids == [images[1]["id"], images[0]["id"], images[1]["id"]]
    assert reply["body"].startswith("整改说明\n[图片]")
    assert reg.get(base + "?manage=true").json()["replies"][0] == reply
    assert other.get(base).json()["replies"] == []
    url = f"{base}/replies/{reply['id']}/images/{images[0]['id']}"
    assert owner.get(url).content == PNG
    assert reg.get(url + "?manage=true").headers["cache-control"] == "no-store"
    assert other.get(url).status_code == 404
    assert association.get(url).status_code == 404
    with SessionLocal() as db:
        assert db.get(ArticleReply, reply["id"]).rich_document == RichDocument.model_validate(
            reply["rich_document"]
        ).model_dump(exclude_none=True)
    bad = owner.post(
        base + "/replies/with-images",
        headers=headers,
        data={"body": "bad reference", "rich_document": json.dumps(doc(paragraph(), image(2)))},
        files=[("files", ("single.png", PNG, "image/png"))],
    )
    assert bad.status_code == 422 and len(owner.get(base).json()["replies"]) == 1


@pytest.mark.parametrize("with_images", [False, True])
def test_projection_failure_preserves_no_half_reply_or_file_and_retry_works(
    notice, monkeypatch, with_images
):
    base, clients, storage = notice
    _, (owner, headers), _, _ = clients

    def submit():
        if with_images:
            return owner.post(
                base + "/replies/with-images",
                headers=headers,
                data={
                    "body": "cache",
                    "rich_document": json.dumps(doc(paragraph("reply"), image(1))),
                },
                files=[("files", ("proof.png", PNG, "image/png"))],
            )
        return owner.post(base + "/replies", headers=headers, json={"rich_document": formatted()})

    def fail(*args, **kwargs):
        raise ValueError("broken reply projection")

    # Existing TestClient raises server exceptions; this deliberately observes the injected failure.
    with monkeypatch.context() as patch:
        patch.setattr(ContentReplyCreated, "model_validate", fail)
        with pytest.raises(ValueError, match="broken reply projection"):
            submit()
    with SessionLocal() as db:
        aid = int(base.rsplit("/", 1)[1])
        assert (
            db.scalar(
                select(func.count()).select_from(ArticleReply).where(ArticleReply.article_id == aid)
            )
            == 0
        )
    assert not list(Path(storage).iterdir())
    assert submit().status_code == 201
    assert len(owner.get(base).json()["replies"]) == 1
