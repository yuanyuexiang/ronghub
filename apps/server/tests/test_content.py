import secrets
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db import SessionLocal
from app.main import app
from app.models import Article, ArticleAction, ArticleReply, ArticleReplyAttachment, now


def test_publication_scheduling_targeting_replies_and_freeze(
    accounts, staff, monkeypatch, tmp_path
):
    from app.config import settings

    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    names, password, _ = accounts
    staffnames, _ = staff
    clients = [TestClient(app, client=("test-" + secrets.token_hex(6), 1)) for _ in range(4)]
    ids = []
    try:
        headers = []
        for c, name in zip(
            clients, [staffnames[0], names[0], names[1], staffnames[1]], strict=True
        ):
            r = c.post("/api/v1/auth/login", json={"username": name, "password": password})
            assert r.status_code == 200
            headers.append({"x-csrf-token": r.json()["csrf_token"]})
        reg, op, other, assoc = clients
        operator_id = op.get("/api/v1/operators").json()["items"][0]["id"]
        body = {
            "kind": "notice",
            "title": "定向整改通知",
            "body": "<script>以纯文本展示</script>",
            "audience": "operator",
            "operator_id": operator_id,
        }
        assert op.post("/api/v1/content", headers=headers[1], json=body).status_code == 403
        r = reg.post("/api/v1/content", headers=headers[0], json=body)
        assert r.status_code == 201
        article = r.json()
        aid = article["id"]
        ids.append(aid)
        url = f"/api/v1/content/{aid}"
        assert op.get(url).status_code == 404
        assert op.get("/api/v1/content?manage=true").status_code == 403
        # Future publication stays invisible to every reader until its scheduled time.
        r = reg.post(
            url + "/actions",
            headers=headers[0],
            json={
                "action": "publish",
                "version": 1,
                "published_at": (now() + timedelta(days=1)).isoformat(),
            },
        )
        assert r.status_code == 200
        assert op.get(url).status_code == 404
        with SessionLocal.begin() as db:
            db.get(Article, aid).published_at = now() - timedelta(seconds=1)
        assert op.get(url).status_code == 200
        assert other.get(url).status_code == 404
        assert assoc.get(url).status_code == 404
        assert reg.patch(url, headers=headers[0], json={**body, "version": 2}).status_code == 409
        assert (
            op.post(url + "/replies", headers=headers[1], json={"body": "企业回复"}).status_code
            == 201
        )
        assert (
            other.post(url + "/replies", headers=headers[2], json={"body": "越权回复"}).status_code
            == 404
        )
        details = reg.get(url + "?manage=true").json()
        assert details["replies"][0]["body"] == "企业回复"
        assert len(details["history"]) == 2
        assert details["replies"][0]["contact_name"] is None
        for invalid in (
            {"contact_name": "联系人"},
            {"contact_name": "姓名", "contact_phone": "123\n456"},
        ):
            assert (
                op.post(
                    url + "/replies", headers=headers[1], json={"body": "invalid", **invalid}
                ).status_code
                == 422
            )
        saved_contact = op.post(
            url + "/replies",
            headers=headers[1],
            json={
                "body": "明确留存",
                "contact_name": "  张先生  ",
                "contact_phone": "  0591-12345678  ",
            },
        )
        assert saved_contact.status_code == 201
        contact_reply = reg.get(url + "?manage=true").json()["replies"][-1]
        assert (
            contact_reply["contact_name"] == "张先生"
            and contact_reply["contact_phone"] == "0591-12345678"
        )
        images_url = url + "/replies/with-images"
        png = b"\x89PNG\r\n\x1a\nexample"
        bad = op.post(
            images_url,
            headers=headers[1],
            data={"body": "should roll back"},
            files=[
                ("files", ("ok.png", png, "image/png")),
                ("files", ("bad.pdf", b"%PDF-1.4bad", "application/pdf")),
            ],
        )
        assert bad.status_code == 415
        assert not list(tmp_path.iterdir())
        assert len(reg.get(url + "?manage=true").json()["replies"]) == 2
        too_large = op.post(
            images_url,
            headers=headers[1],
            data={"body": "too large"},
            files={"files": ("large.png", png + b"x" * (5 * 1024 * 1024), "image/png")},
        )
        assert too_large.status_code == 413 and not list(tmp_path.iterdir())
        cross = other.post(
            images_url,
            headers=headers[2],
            data={"body": "cross"},
            files={"files": ("cross.png", png, "image/png")},
        )
        assert cross.status_code == 404
        saved = op.post(
            images_url,
            headers=headers[1],
            data={"body": "图文核查结果", "contact_name": "李女士", "contact_phone": "13800000000"},
            files=[
                ("files", ("one.png", png, "image/png")),
                ("files", ("two.png", png, "image/png")),
            ],
        )
        assert saved.status_code == 201
        rid = saved.json()["id"]
        reply = op.get(url).json()["replies"][-1]
        assert reply["body"] == "图文核查结果" and len(reply["images"]) == 2
        assert reply["contact_name"] == "李女士" and reply["contact_phone"] == "13800000000"
        assert "storage_key" not in reply["images"][0]
        image_id = reply["images"][0]["id"]
        image_url = f"{url}/replies/{rid}/images/{image_id}"
        assert op.get(image_url).content == png
        assert other.get(image_url).status_code == 404
        assert assoc.get(image_url).status_code == 404
        assert reg.get(image_url).status_code == 404
        assert reg.get(image_url + "?manage=true").status_code == 200
        assert len(list(tmp_path.iterdir())) == 2
        assert (
            reg.post(
                url + "/actions", headers=headers[0], json={"action": "withdraw", "version": 1}
            ).status_code
            == 409
        )
        assert (
            reg.post(
                url + "/actions", headers=headers[0], json={"action": "withdraw", "version": 2}
            ).status_code
            == 200
        )
        assert op.get(url).status_code == 404
        assert op.get(image_url).status_code == 404
        assert reg.get(image_url + "?manage=true").status_code == 200
        # Shared published announcements are visible to the association too.
        r = reg.post(
            "/api/v1/content",
            headers=headers[0],
            json={
                **body,
                "kind": "announcement",
                "title": "三端公告",
                "audience": "all",
                "operator_id": None,
            },
        )
        public = r.json()["id"]
        ids.append(public)
        assert (
            reg.post(
                f"/api/v1/content/{public}/actions",
                headers=headers[0],
                json={"action": "publish", "version": 1},
            ).status_code
            == 200
        )
        assert assoc.get(f"/api/v1/content/{public}").status_code == 200
    finally:
        for c in clients:
            c.close()
        with SessionLocal.begin() as db:
            from sqlalchemy import select

            replies = select(ArticleReply.id).where(ArticleReply.article_id.in_(ids))
            db.execute(
                delete(ArticleReplyAttachment).where(ArticleReplyAttachment.reply_id.in_(replies))
            )
            db.execute(delete(ArticleReply).where(ArticleReply.article_id.in_(ids)))
            db.execute(delete(ArticleAction).where(ArticleAction.article_id.in_(ids)))
            db.execute(delete(Article).where(Article.id.in_(ids)))


def test_shared_notice_reply_image_privacy_and_reply_permission(
    accounts, staff, monkeypatch, tmp_path
):
    from sqlalchemy import select

    from app.config import settings
    from app.models import RoleProfile, User

    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    names, password, _ = accounts
    staffnames, _ = staff
    clients = [TestClient(app, client=("images-" + secrets.token_hex(6), 1)) for _ in range(5)]
    aid = profile_id = None
    try:
        reg, owner, other, assoc, anonymous = clients
        headers = []
        for client, username in zip(
            clients[:4], [staffnames[0], names[0], names[1], staffnames[1]], strict=True
        ):
            response = client.post(
                "/api/v1/auth/login", json={"username": username, "password": password}
            )
            assert response.status_code == 200
            headers.append({"x-csrf-token": response.json()["csrf_token"]})
        article = reg.post(
            "/api/v1/content",
            headers=headers[0],
            json={
                "kind": "notice",
                "title": "shared image privacy",
                "body": "share notice only, private replies",
                "audience": "all",
            },
        ).json()
        aid = article["id"]
        base = f"/api/v1/content/{aid}"
        assert (
            reg.post(
                base + "/actions", headers=headers[0], json={"action": "publish", "version": 1}
            ).status_code
            == 200
        )
        png = b"\x89PNG\r\n\x1a\nfixture"
        too_many = owner.post(
            base + "/replies/with-images",
            headers=headers[1],
            data={"body": "limit"},
            files=[("files", (f"{i}.png", png, "image/png")) for i in range(11)],
        )
        assert too_many.status_code == 422
        assert not list(tmp_path.iterdir())
        saved = owner.post(
            base + "/replies/with-images",
            headers=headers[1],
            data={"body": "private image reply"},
            files={"files": ("private.png", png, "image/png")},
        )
        assert saved.status_code == 201
        reply = owner.get(base).json()["replies"][0]
        image = reply["images"][0]
        image_url = f"{base}/replies/{reply['id']}/images/{image['id']}"
        assert owner.get(image_url).status_code == 200
        assert anonymous.get(image_url).status_code == 401
        for client in [other, assoc]:
            assert client.get(base).status_code == 200
            assert client.get(base).json()["replies"] == []
            assert client.get(image_url).status_code == 404
            assert client.get(image_url + "?manage=true").status_code == 403
        assert reg.get(image_url).status_code == 200
        with SessionLocal.begin() as db:
            db.scalar(select(User).where(User.username == staffnames[0])).allowed_districts = []
        assert reg.get(base).json()["replies"] == []
        assert reg.get(image_url).status_code == 404
        with SessionLocal.begin() as db:
            db.scalar(select(User).where(User.username == staffnames[0])).allowed_districts = [
                "测试"
            ]
        assert reg.get(image_url).status_code == 200
        with SessionLocal.begin() as db:
            db.scalar(select(User).where(User.username == staffnames[0])).allowed_districts = None
            profile = RoleProfile(
                name="test-image-read-" + secrets.token_hex(6),
                base_role="operator",
                permissions=["stations.read", "content.read"],
            )
            db.add(profile)
            db.flush()
            profile_id = profile.id
            db.scalar(select(User).where(User.username == names[0])).role_profile_id = profile.id
        assert owner.get(image_url).status_code == 200
        assert (
            owner.post(
                base + "/replies/with-images",
                headers=headers[1],
                data={"body": "forbidden"},
                files={"files": ("forbidden.png", png, "image/png")},
            ).status_code
            == 403
        )
        assert len(reg.get(base).json()["replies"]) == 1
        assert len(list(tmp_path.iterdir())) == 1
    finally:
        for client in clients:
            client.close()
        with SessionLocal.begin() as db:
            if aid:
                replies = select(ArticleReply.id).where(ArticleReply.article_id == aid)
                db.execute(
                    delete(ArticleReplyAttachment).where(
                        ArticleReplyAttachment.reply_id.in_(replies)
                    )
                )
                db.execute(delete(ArticleReply).where(ArticleReply.article_id == aid))
                db.execute(delete(ArticleAction).where(ArticleAction.article_id == aid))
                db.execute(delete(Article).where(Article.id == aid))
            if profile_id:
                db.scalar(select(User).where(User.username == names[0])).role_profile_id = None
                db.flush()
                db.execute(delete(RoleProfile).where(RoleProfile.id == profile_id))
