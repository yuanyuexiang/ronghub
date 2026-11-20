import csv
import io
import secrets

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, insert, select

from app.db import SessionLocal
from app.main import app
from app.models import (
    Article,
    ArticleAction,
    ArticleAttachment,
    ArticleReply,
    Audit,
    NoticeDelivery,
    User,
    now,
)


def test_batch_versions_atomic_publication_soft_delete_and_metadata_export(
    accounts, staff, monkeypatch, tmp_path
):
    from app.config import settings

    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    prefix = "content-ops-" + secrets.token_hex(8)
    ids = []
    clients = [TestClient(app, client=(prefix + str(i), 1)) for i in range(2)]
    try:
        reg, own = clients
        headers = []
        for client, name in zip(clients, [staff[0][0], accounts[0][0]], strict=True):
            login = client.post(
                "/api/v1/auth/login", json={"username": name, "password": accounts[1]}
            )
            assert login.status_code == 200
            headers.append({"x-csrf-token": login.json()["csrf_token"]})
        with SessionLocal() as db:
            oid = db.scalar(select(User.operator_id).where(User.username == accounts[0][0]))

        def create():
            result = reg.post(
                "/api/v1/content",
                headers=headers[0],
                json={
                    "kind": "notice",
                    "title": prefix,
                    "body": "private body must not export",
                    "audience": "operator",
                    "recipient_ids": [oid],
                },
            )
            assert result.status_code == 201
            article = result.json()
            ids.append(article["id"])
            return {"id": article["id"], "version": article["version"]}

        a, b = create(), create()
        base_query = {"manage": True, "q": prefix}
        matched = reg.get("/api/v1/content", params={**base_query, "kind": "notice"})
        assert matched.status_code == 200 and matched.json()["total"] == 2
        assert all(item["kind"] == "notice" for item in matched.json()["items"])
        assert (
            reg.get("/api/v1/content", params={**base_query, "kind": "news"}).json()["total"] == 0
        )
        assert (
            reg.get("/api/v1/content", params={**base_query, "kind": "invalid"}).status_code == 422
        )
        csv_result = reg.get("/api/v1/content/export", params={"q": prefix, "kind": "notice"})
        assert csv_result.status_code == 200
        assert len(list(csv.reader(io.StringIO(csv_result.text)))) == 3
        csv_empty = reg.get("/api/v1/content/export", params={"q": prefix, "kind": "news"})
        assert csv_empty.status_code == 200
        assert len(list(csv.reader(io.StringIO(csv_empty.text)))) == 1
        url = "/api/v1/content/batch-actions"
        assert (
            own.post(
                url, headers=headers[1], json={"action": "publish", "items": [a, b]}
            ).status_code
            == 403
        )
        assert (
            reg.post(
                url, headers=headers[0], json={"action": "publish", "items": [a, a]}
            ).status_code
            == 422
        )
        assert (
            reg.post(
                url,
                headers=headers[0],
                json={"action": "publish", "items": [a, {**b, "version": 2}]},
            ).status_code
            == 409
        )
        assert reg.get(f"/api/v1/content/{a['id']}?manage=true").json()["status"] == "draft"
        # A bad second file must roll back the first article, its history and recipient rows.
        png = b"\x89PNG\r\n\x1a\nproof"
        uploaded = reg.post(
            f"/api/v1/content/{b['id']}/attachments",
            headers=headers[0],
            data={"version": 1},
            files={"file": ("proof.png", png, "image/png")},
        )
        assert uploaded.status_code == 201
        b["version"] = 2
        path = next(tmp_path.iterdir())
        path.write_bytes(b"bad")
        failed = reg.post(url, headers=headers[0], json={"action": "publish", "items": [a, b]})
        assert failed.status_code == 409, failed.text
        with SessionLocal() as db:
            assert db.get(Article, a["id"]).status == "draft"
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(ArticleAction)
                    .where(ArticleAction.article_id == a["id"])
                )
                == 1
            )
            assert (
                db.scalar(select(NoticeDelivery.id).where(NoticeDelivery.article_id.in_(ids)))
                is None
            )
        path.write_bytes(png)
        published = reg.post(url, headers=headers[0], json={"action": "publish", "items": [b, a]})
        assert published.status_code == 200, published.text
        assert published.json()["total"] == 2
        assert (
            reg.post(
                url, headers=headers[0], json={"action": "publish", "items": [a, b]}
            ).status_code
            == 409
        )
        assert (
            reg.post(
                f"/api/v1/content/{a['id']}/actions",
                headers=headers[0],
                json={"action": "delete", "version": 2},
            ).status_code
            == 409
        )
        c, d = create(), create()
        removed = reg.post(url, headers=headers[0], json={"action": "delete", "items": [c, d]})
        assert removed.status_code == 200
        assert reg.get("/api/v1/content", params={"manage": True, "q": prefix}).json()["total"] == 2
        assert (
            reg.get(
                "/api/v1/content", params={"manage": True, "q": prefix, "include_deleted": True}
            ).json()["total"]
            == 4
        )
        detail = reg.get(f"/api/v1/content/{c['id']}?manage=true").json()
        assert detail["status"] == "deleted" and detail["history"][-1]["action"] == "delete"
        assert own.get(f"/api/v1/content/{c['id']}").status_code == 404
        for path_url in ("/api/v1/content/export", "/api/v1/content/notice-records/export"):
            assert own.get(path_url).status_code == 403
            exported = reg.get(path_url, params={"q": prefix})
            assert exported.status_code == 200, exported.text
            assert len(list(csv.reader(io.StringIO(exported.text.lstrip("\ufeff"))))[1:]) == 2
            assert "private body must not export" not in exported.text
            assert "contact_phone" not in exported.text
        with SessionLocal.begin() as db:
            db.get(Article, a["id"]).title = "=" + prefix
        export_formula = reg.get("/api/v1/content/export", params={"q": prefix})
        assert "'=" + prefix in export_formula.text
    finally:
        for client in clients:
            client.close()
        with SessionLocal.begin() as db:
            db.execute(delete(ArticleAttachment).where(ArticleAttachment.article_id.in_(ids)))
            db.execute(delete(ArticleReply).where(ArticleReply.article_id.in_(ids)))
            db.execute(delete(ArticleAction).where(ArticleAction.article_id.in_(ids)))
            db.execute(delete(Article).where(Article.id.in_(ids)))


def test_content_exports_reject_more_than_ten_thousand_rows(accounts, staff):
    prefix = "content-export-limit-" + secrets.token_hex(8)
    client = TestClient(app, client=(prefix, 1))
    ids = []
    try:
        assert (
            client.post(
                "/api/v1/auth/login", json={"username": staff[0][0], "password": accounts[1]}
            ).status_code
            == 200
        )
        with SessionLocal.begin() as db:
            uid = db.scalar(select(User.id).where(User.username == staff[0][0]))
            oid = db.scalar(select(User.operator_id).where(User.username == accounts[0][0]))
            ids = list(
                db.scalars(
                    insert(Article).returning(Article.id),
                    [
                        {
                            "kind": "notice",
                            "title": prefix,
                            "body": "body",
                            "audience": "operator",
                            "recipient_ids": [oid],
                            "status": "published",
                            "published_at": now(),
                            "created_by": uid,
                        }
                        for _ in range(10001)
                    ],
                )
            )
            db.execute(
                insert(NoticeDelivery),
                [
                    {
                        "article_id": aid,
                        "operator_id": oid,
                        "operator_name": "company",
                        "contacts": [],
                        "publication_version": 2,
                        "scheduled_at": now(),
                    }
                    for aid in ids
                ],
            )
        for endpoint in ("export", "notice-records/export"):
            result = client.get("/api/v1/content/" + endpoint, params={"q": prefix})
            assert result.status_code == 422, result.text[:200]
        with SessionLocal() as db:
            assert (
                db.scalar(
                    select(Audit.id).where(
                        Audit.user_id == uid,
                        Audit.action.in_(["content.export", "content.notice-records.export"]),
                    )
                )
                is None
            )
    finally:
        client.close()
        with SessionLocal.begin() as db:
            db.execute(delete(Article).where(Article.id.in_(ids)))
