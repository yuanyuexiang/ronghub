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


def test_information_group_filters_list_and_export_without_notifications_or_protocols(
    accounts, staff
):
    prefix = "info-group-" + secrets.token_hex(6)
    with TestClient(app, client=(prefix, 1)) as client:
        assert (
            client.post(
                "/api/v1/auth/login", json={"username": staff[0][0], "password": accounts[1]}
            ).status_code
            == 200
        )
        with SessionLocal.begin() as db:
            uid = db.scalar(select(User.id).where(User.username == staff[0][0]))
            rows = [
                Article(
                    kind=kind,
                    title=prefix,
                    body="test",
                    audience="all",
                    status="draft",
                    created_by=uid,
                )
                for kind in ["announcement", "news", "contact", "protocol"]
            ]
            db.add_all(rows)
            db.flush()
            ids = [row.id for row in rows]
        try:
            response = client.get(
                "/api/v1/content", params={"manage": True, "q": prefix, "kind": "information"}
            )
            assert response.status_code == 200 and response.json()["total"] == 3
            assert {row["kind"] for row in response.json()["items"]} == {
                "announcement",
                "news",
                "contact",
            }
            exported = client.get(
                "/api/v1/content/export", params={"q": prefix, "kind": "information"}
            )
            assert exported.status_code == 200
            records = list(csv.reader(io.StringIO(exported.text)))
            assert len(records) == 4 and "protocol" not in exported.text
        finally:
            with SessionLocal.begin() as db:
                db.execute(delete(Article).where(Article.id.in_(ids)))


def test_operator_notice_inbox_combines_visible_notices_and_own_replies(accounts, staff):
    from datetime import UTC, datetime, timedelta

    names, password, _stations = accounts
    prefix = "operator-inbox-" + secrets.token_hex(8)
    ids = []
    published = datetime(2026, 11, 19, 16, tzinfo=UTC)
    clients = [TestClient(app, client=(prefix + str(i), 1)) for i in range(3)]
    try:
        for client, username in zip(clients, [*names, staff[0][0]], strict=True):
            assert (
                client.post(
                    "/api/v1/auth/login", json={"username": username, "password": password}
                ).status_code
                == 200
            )
        own, other, regulator = clients
        with SessionLocal.begin() as db:
            owners = [
                db.scalar(select(User).where(User.username == username)) for username in names
            ]
            reg_id = db.scalar(select(User.id).where(User.username == staff[0][0]))
            operator_a, operator_b = [user.operator_id for user in owners]

            def article(label, **overrides):
                fields = {
                    "kind": "notice",
                    "title": f"{prefix}-{label}",
                    "body": "confidential-body",
                    "audience": "operator",
                    "created_by": reg_id,
                    "published_at": published,
                    "status": "published",
                }
                row = Article(**(fields | overrides))
                db.add(row)
                db.flush()
                ids.append(row.id)
                return row

            general = article("general")
            legacy = article("legacy", operator_id=operator_a)
            targeted = article("targeted", recipient_ids=[operator_a])
            db.add(
                NoticeDelivery(
                    article_id=targeted.id,
                    operator_id=operator_a,
                    operator_name="收件名称",
                    contacts=[],
                    publication_version=2,
                    scheduled_at=published,
                )
            )
            article("outside", operator_id=operator_b)
            article("scheduled", published_at=now() + timedelta(days=1))
            article("withdrawn", status="withdrawn")
            article("news", kind="news")
            article("outside-window", published_at=published - timedelta(seconds=1))
            stamp = published + timedelta(hours=1)
            db.add_all(
                [
                    ArticleReply(
                        article_id=general.id,
                        operator_id=owner.operator_id,
                        actor_id=owner.id,
                        body="confidential-reply",
                        created_at=stamp + timedelta(hours=index),
                    )
                    for index, owner in enumerate(owners)
                ]
            )
            general_id, legacy_id, targeted_id = general.id, legacy.id, targeted.id
        path = "/api/v1/content/operator-notices"
        params = {"q": prefix, "published_from": "2026-11-20", "published_to": "2026-11-20"}
        response = own.get(path, params=params)
        assert response.status_code == 200, response.text
        rows = {row["article_id"]: row for row in response.json()["items"]}
        assert set(rows) == {general_id, legacy_id, targeted_id}
        assert rows[general_id]["reply_count"] == 1
        assert rows[general_id]["latest_reply_at"] == stamp.isoformat()
        assert rows[targeted_id]["latest_reply_at"] is None
        assert own.get(path, params={**params, "replied": True}).json()["total"] == 1
        assert own.get(path, params={**params, "replied": False}).json()["total"] == 2
        other_ids = {row["article_id"] for row in other.get(path, params=params).json()["items"]}
        assert (
            general_id in other_ids and targeted_id not in other_ids and legacy_id not in other_ids
        )
        assert regulator.get(path, params=params).status_code == 403
        assert regulator.get(path + "/export", params=params).status_code == 403
        assert own.get("/api/v1/content/notice-records/export").status_code == 403
        exported = own.get(path + "/export", params={**params, "replied": False})
        assert exported.status_code == 200, exported.text
        assert prefix + "-targeted" in exported.text and prefix + "-legacy" in exported.text
        for private in ("confidential", prefix + "-outside", prefix + "-general"):
            assert private not in exported.text
        assert own.get(path, params={**params, "published_from": "2026-11-21"}).status_code == 422
    finally:
        for client in clients:
            client.close()
        with SessionLocal.begin() as db:
            db.execute(delete(ArticleReply).where(ArticleReply.article_id.in_(ids)))
            db.execute(delete(Article).where(Article.id.in_(ids)))
