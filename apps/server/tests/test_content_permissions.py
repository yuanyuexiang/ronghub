import secrets
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import (
    Article,
    ArticleAction,
    ArticleReply,
    ArticleReplyAttachment,
    Audit,
    LoginSession,
    RoleProfile,
    User,
)


def test_content_management_creation_editing_publication_withdrawal_and_reply_permissions(
    accounts, staff, business_login
):
    names, password, _ = accounts
    profiles, users, clients, articles = [], [], [], []

    def login(name):
        c = TestClient(app, client=("content-grant-" + secrets.token_hex(8), 1))
        clients.append(c)
        r = business_login(c, name, password)
        assert r.status_code == 200
        return c, {"x-csrf-token": r.json()["csrf_token"]}

    root, rh = login(staff[0][0])
    company, oh = login(names[0])
    other, _ = login(names[1])
    with SessionLocal() as db:
        company_id = db.scalar(select(User.operator_id).where(User.username == names[0]))
    try:

        def actor(cap=None, districts=None):
            p = root.post(
                "/api/v1/roles",
                headers=rh,
                json={
                    "name": "content-role-" + secrets.token_hex(8),
                    "base_role": "regulator",
                    "permissions": sorted(
                        {"stations.read", "content.read"}
                        | ({"content.manage", cap} if cap else set())
                    ),
                },
            )
            assert p.status_code == 201, p.text
            profiles.append(p.json()["id"])
            name = "content-grant-" + secrets.token_hex(8)
            u = root.post(
                "/api/v1/accounts",
                headers=rh,
                json={
                    "username": name,
                    "password": password,
                    "role_profile_id": profiles[-1],
                    "allowed_districts": districts,
                },
            )
            assert u.status_code == 201, u.text
            users.append(u.json()["id"])
            return login(name)

        viewer, vh = actor("content.manage")
        creator, ch = actor("content.create")
        editor, eh = actor("content.edit")
        publisher, ph = actor("content.publish")
        withdrawer, wh = actor("content.withdraw")
        reader, _ = actor()
        regional, _ = actor("content.manage", ["测试"])
        body = {
            "kind": "notice",
            "title": "独立岗位内容",
            "body": "草稿正文",
            "audience": "operator",
            "operator_id": company_id,
        }
        assert publisher.post("/api/v1/content", headers=ph, json=body).status_code == 403
        created = creator.post("/api/v1/content", headers=ch, json=body)
        assert created.status_code == 201
        aid = created.json()["id"]
        articles.append(aid)
        url = f"/api/v1/content/{aid}"
        assert reader.get(url).status_code == 404
        for true in ["true", "1", "True", "TRUE", "yes", "on"]:
            assert reader.get(url + "?manage=" + true).status_code == 403
            assert reader.get("/api/v1/content?manage=" + true).status_code == 403
            assert regional.get(url + "?manage=" + true).status_code == 403
        assert viewer.get(url + "?manage=TRUE").status_code == 200
        assert viewer.get("/api/v1/content/operator-options").status_code == 200
        deleter, dh = actor("content.delete")
        exporter, xh = actor("content.export")
        for path in ("/api/v1/content/export", "/api/v1/content/notice-records/export"):
            assert viewer.get(path).status_code == 403
            assert reader.get(path).status_code == 403
            assert regional.get(path).status_code == 403
            assert exporter.get(path).status_code == 200
        deletable = creator.post("/api/v1/content", headers=ch, json=body).json()
        articles.append(deletable["id"])
        delete_path = f"/api/v1/content/{deletable['id']}/actions"
        assert (
            viewer.post(
                delete_path, headers=vh, json={"action": "delete", "version": 1}
            ).status_code
            == 403
        )
        assert (
            exporter.post(
                delete_path, headers=xh, json={"action": "delete", "version": 1}
            ).status_code
            == 403
        )
        assert (
            deleter.post(
                delete_path, headers=dh, json={"action": "delete", "version": 1}
            ).status_code
            == 200
        )
        assert regional.get("/api/v1/content/operator-options").status_code == 403
        assert regional.get("/api/v1/content/notice-records?manage=TRUE").status_code == 403
        assert viewer.patch(url, headers=vh, json={**body, "version": 1}).status_code == 403
        assert creator.patch(url, headers=ch, json={**body, "version": 1}).status_code == 403
        edited = editor.patch(url, headers=eh, json={**body, "body": "已编辑正文", "version": 1})
        assert edited.status_code == 200

        def action(c, h, verb, version):
            return c.post(url + "/actions", headers=h, json={"action": verb, "version": version})

        assert action(editor, eh, "publish", 2).status_code == 403
        assert action(viewer, vh, "publish", 2).status_code == 403
        assert action(publisher, ph, "publish", 2).status_code == 200
        assert company.get(url).status_code == 200
        assert other.get(url).status_code == 404
        assert editor.patch(url, headers=eh, json={**body, "version": 3}).status_code == 409
        replied = company.post(url + "/replies", headers=oh, json={"body": "已阅并整改"})
        assert replied.status_code == 201
        assert action(publisher, ph, "withdraw", 3).status_code == 403
        assert action(withdrawer, wh, "withdraw", 3).status_code == 200
        assert company.get(url).status_code == 404
        assert company.post(url + "/replies", headers=oh, json={"body": "迟到"}).status_code == 404
        assert viewer.get(url + "?manage=yes").json()["replies"][0]["body"] == "已阅并整改"
        assert action(withdrawer, wh, "unknown", 4).status_code == 422
        for caps, status in [
            (["stations.read", "content.create"], 422),
            (["stations.read", "content.read", "content.publish"], 422),
            (["stations.read", "content.read", "content.write"], 201),
        ]:
            r = root.post(
                "/api/v1/roles",
                headers=rh,
                json={
                    "name": "content-role-" + secrets.token_hex(8),
                    "base_role": "regulator",
                    "permissions": caps,
                },
            )
            assert r.status_code == status, r.text
            if status == 201:
                profiles.append(r.json()["id"])
                assert set(r.json()["permissions"]) == {
                    "stations.read",
                    "content.read",
                    "content.manage",
                    "content.create",
                    "content.edit",
                    "content.publish",
                    "content.withdraw",
                }
        with SessionLocal() as db:
            logs = list(
                db.scalars(
                    select(Audit)
                    .where(
                        Audit.object_id == aid,
                        Audit.action.in_(
                            [
                                "content.create",
                                "content.edit",
                                "content.publish",
                                "content.withdraw",
                            ]
                        ),
                    )
                    .order_by(Audit.id)
                )
            )
            assert [log.action for log in logs] == [
                "content.create",
                "content.edit",
                "content.publish",
                "content.withdraw",
            ]
            assert logs[0].detail["before"] == {}
            assert logs[-1].detail["before"]["status"] == "published"
            assert logs[-1].detail["after"]["status"] == "withdrawn"
            assert all("body" not in log.detail["after"] for log in logs)
    finally:
        for c in clients:
            c.close()
        with SessionLocal.begin() as db:
            replies = select(ArticleReply.id).where(ArticleReply.article_id.in_(articles))
            for image in db.scalars(
                select(ArticleReplyAttachment).where(ArticleReplyAttachment.reply_id.in_(replies))
            ):
                (Path(settings.storage_path).resolve() / image.storage_key).unlink(missing_ok=True)
            db.execute(
                delete(ArticleReplyAttachment).where(ArticleReplyAttachment.reply_id.in_(replies))
            )
            db.execute(delete(ArticleReply).where(ArticleReply.article_id.in_(articles)))
            db.execute(delete(ArticleAction).where(ArticleAction.article_id.in_(articles)))
            db.execute(delete(Article).where(Article.id.in_(articles)))
            db.execute(delete(LoginSession).where(LoginSession.user_id.in_(users)))
            db.execute(delete(Audit).where(Audit.user_id.in_(users)))
            db.execute(delete(User).where(User.id.in_(users)))
            db.execute(delete(RoleProfile).where(RoleProfile.id.in_(profiles)))
