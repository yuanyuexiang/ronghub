import secrets
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.main import app
from app.models import Article, ArticleAction, ArticleReply, NoticeDelivery, Operator, User, now


def test_explicit_recipients_freeze_contacts_scope_replies_and_legacy(accounts, staff):
    names, password, _ = accounts
    staffnames, _ = staff
    clients = [TestClient(app, client=("notice-" + secrets.token_hex(8), 1)) for _ in range(4)]
    ids = []
    old_contacts = {}
    try:
        headers = []
        for client, name in zip(
            clients, [staffnames[0], names[0], names[1], staffnames[1]], strict=True
        ):
            response = client.post(
                "/api/v1/auth/login", json={"username": name, "password": password}
            )
            assert response.status_code == 200
            headers.append({"x-csrf-token": response.json()["csrf_token"]})
        reg, op, other, assoc = clients
        with SessionLocal.begin() as db:
            company_ids = [
                db.scalar(select(User.operator_id).where(User.username == name)) for name in names
            ]
            for i, oid in enumerate(company_ids):
                row = db.get(Operator, oid)
                old_contacts[oid] = row.contacts
                row.contacts = [
                    {"name": "历史联系人" + str(i), "phone": "1380000000" + str(i), "primary": True}
                ]
        assert reg.get("/api/v1/content/operator-options").status_code == 200
        assert op.get("/api/v1/content/operator-options").status_code == 403
        body = {
            "kind": "notice",
            "title": "多企业通知",
            "body": "共享冻结正文",
            "audience": "operator",
            "recipient_ids": company_ids,
        }
        for bad in ([], [company_ids[0], company_ids[0]], [-1], [True], ["1"], [2147483647]):
            assert (
                reg.post(
                    "/api/v1/content", headers=headers[0], json={**body, "recipient_ids": bad}
                ).status_code
                == 422
            )
        response = reg.post("/api/v1/content", headers=headers[0], json=body)
        assert response.status_code == 201, response.text
        aid = response.json()["id"]
        ids.append(aid)
        url = f"/api/v1/content/{aid}"
        assert reg.get(url + "?manage=true").json()["recipient_ids"] == sorted(company_ids)
        assert op.get(url).status_code == 404
        stamp = now() + timedelta(hours=1)
        published = reg.post(
            url + "/actions",
            headers=headers[0],
            json={"action": "publish", "version": 1, "published_at": stamp.isoformat()},
        )
        assert published.status_code == 200, published.text
        managed = reg.get(url + "?manage=true").json()
        assert len(managed["deliveries"]) == 2 and {d["state"] for d in managed["deliveries"]} == {
            "scheduled"
        }
        assert all(d["publication_version"] == 2 for d in managed["deliveries"])
        assert op.get(url).status_code == 404
        assert (
            reg.post(
                url + "/actions", headers=headers[0], json={"action": "publish", "version": 1}
            ).status_code
            == 409
        )
        with SessionLocal.begin() as db:
            db.get(Article, aid).published_at = now() - timedelta(seconds=1)
            for oid in company_ids:
                db.get(Operator, oid).contacts = [{"name": "后续联系人", "phone": "999"}]
        assert assoc.get(url).status_code == 404
        for client, oid in zip([op, other], company_ids, strict=True):
            result = client.get(url).json()
            assert result["recipient_ids"] is None
            assert len(result["deliveries"]) == 1 and result["deliveries"][0]["operator_id"] == oid
            assert result["deliveries"][0]["contacts"][0]["name"].startswith("历史联系人")
            assert result.get("history") is None
        assert (
            op.post(
                url + "/replies",
                headers=headers[1],
                json={"body": "甲回复", "contact_name": "甲", "contact_phone": "123"},
            ).status_code
            == 201
        )
        assert other.get(url).json()["replies"] == []
        rows = reg.get(url + "?manage=true").json()["deliveries"]
        assert {row["operator_id"]: row["reply_count"] for row in rows} == {
            company_ids[0]: 1,
            company_ids[1]: 0,
        }
        endpoint = "/api/v1/content/notice-records"
        assert assoc.get(endpoint).status_code == 403
        assert op.get(endpoint + "?manage=TRUE").status_code == 403
        managed_queue = reg.get(
            endpoint, params={"manage": True, "article_id": aid, "page_size": 1}
        )
        assert managed_queue.status_code == 200, managed_queue.text
        assert managed_queue.json()["total"] == 2 and len(managed_queue.json()["items"]) == 1
        assert (
            len(
                reg.get(
                    endpoint, params={"manage": True, "article_id": aid, "page": 2, "page_size": 1}
                ).json()["items"]
            )
            == 1
        )
        for client, replied_count in [(op, 1), (other, 0)]:
            result = client.get(endpoint, params={"article_id": aid, "replied": True})
            assert result.status_code == 200, result.text
            assert result.json()["total"] == replied_count
            assert (
                client.get(endpoint, params={"article_id": aid, "replied": False}).json()["total"]
                == 1 - replied_count
            )
        assert (
            reg.get(endpoint, params={"manage": True, "article_id": aid, "q": "%"}).json()["total"]
            == 0
        )
        # A company not in the recipient set stays invisible even though operator_id is NULL.
        single = reg.post(
            "/api/v1/content", headers=headers[0], json={**body, "recipient_ids": [company_ids[0]]}
        ).json()
        ids.append(single["id"])
        single_url = f"/api/v1/content/{single['id']}"
        assert (
            reg.post(
                single_url + "/actions",
                headers=headers[0],
                json={"action": "publish", "version": 1},
            ).status_code
            == 200
        )
        assert other.get(single_url).status_code == 404
        assert all(
            row["id"] != single["id"] for row in other.get("/api/v1/content").json()["items"]
        )
        assert (
            reg.post(
                url + "/actions", headers=headers[0], json={"action": "withdraw", "version": 2}
            ).status_code
            == 200
        )
        assert op.get(url).status_code == 404
        assert {row["state"] for row in reg.get(url + "?manage=true").json()["deliveries"]} == {
            "withdrawn"
        }
        # Existing broadcast semantics remain; no fabricated historical delivery cohort.
        legacy = reg.post(
            "/api/v1/content", headers=headers[0], json={**body, "recipient_ids": None}
        ).json()
        ids.append(legacy["id"])
        legacy_url = f"/api/v1/content/{legacy['id']}"
        assert (
            reg.post(
                legacy_url + "/actions",
                headers=headers[0],
                json={"action": "publish", "version": 1},
            ).status_code
            == 200
        )
        assert op.get(legacy_url).json()["deliveries"] == []
        assert other.get(legacy_url).status_code == 200
    finally:
        for client in clients:
            client.close()
        with SessionLocal.begin() as db:
            db.execute(delete(ArticleReply).where(ArticleReply.article_id.in_(ids)))
            db.execute(delete(ArticleAction).where(ArticleAction.article_id.in_(ids)))
            db.execute(delete(Article).where(Article.id.in_(ids)))
            assert (
                db.scalar(select(NoticeDelivery.id).where(NoticeDelivery.article_id.in_(ids)))
                is None
            )
            for oid, contacts in old_contacts.items():
                db.get(Operator, oid).contacts = contacts


def test_notice_options_and_records_reach_beyond_first_page(accounts, staff):
    _names, password, _ = accounts
    reg = TestClient(app, client=("notice-pages-" + secrets.token_hex(8), 1))
    prefix = "notice-pages-" + secrets.token_hex(8)
    operators, articles = [], []
    try:
        login = reg.post("/api/v1/auth/login", json={"username": staff[0][0], "password": password})
        assert login.status_code == 200
        with SessionLocal.begin() as db:
            uid = db.scalar(select(User.id).where(User.username == staff[0][0]))
            for i in range(25):
                company = Operator(name=f"{prefix}-{i:02}", contacts=[])
                db.add(company)
                db.flush()
                operators.append(company.id)
                article = Article(
                    kind="notice",
                    title=f"{prefix}-{i:02}",
                    body="分页正文",
                    audience="operator",
                    recipient_ids=[company.id],
                    status="published",
                    published_at=now() - timedelta(seconds=1),
                    created_by=uid,
                )
                db.add(article)
                db.flush()
                articles.append(article.id)
                db.add(
                    NoticeDelivery(
                        article_id=article.id,
                        operator_id=company.id,
                        operator_name=company.name,
                        contacts=[],
                        publication_version=2,
                        scheduled_at=article.published_at,
                    )
                )
        for endpoint in ("operator-options", "notice-records"):
            url = "/api/v1/content/" + endpoint
            first = reg.get(url, params={"manage": True, "q": prefix}).json()
            second = reg.get(url, params={"manage": True, "q": prefix, "page": 2}).json()
            assert first["total"] == second["total"] == 25
            assert len(first["items"]) == 20 and len(second["items"]) == 5
            assert not ({r["id"] for r in first["items"]} & {r["id"] for r in second["items"]})
            assert reg.get(url, params={"manage": True, "q": prefix + "%"}).json()["total"] == 0
        # A draft must not publish if a selected company was disabled afterwards.
        headers = {"x-csrf-token": login.json()["csrf_token"]}
        draft = reg.post(
            "/api/v1/content",
            headers=headers,
            json={
                "kind": "notice",
                "title": prefix,
                "body": "关闭企业测试",
                "audience": "operator",
                "recipient_ids": operators[:2],
            },
        )
        assert draft.status_code == 201
        aid = draft.json()["id"]
        articles.append(aid)
        with SessionLocal.begin() as db:
            db.get(Operator, operators[1]).enabled = False
        publish = reg.post(
            f"/api/v1/content/{aid}/actions",
            headers=headers,
            json={"action": "publish", "version": 1},
        )
        assert publish.status_code == 422
        with SessionLocal() as db:
            assert db.get(Article, aid).status == "draft"
            assert (
                db.scalar(select(NoticeDelivery.id).where(NoticeDelivery.article_id == aid)) is None
            )
    finally:
        reg.close()
        with SessionLocal.begin() as db:
            db.execute(delete(ArticleAction).where(ArticleAction.article_id.in_(articles)))
            db.execute(delete(Article).where(Article.id.in_(articles)))
            db.execute(delete(Operator).where(Operator.id.in_(operators)))
