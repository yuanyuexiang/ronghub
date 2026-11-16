import secrets
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import (
    Audit,
    LoginSession,
    RatingAction,
    RatingApplication,
    RatingAttachment,
    RatingStandard,
    RoleProfile,
    Station,
    User,
)


def test_rating_review_publish_and_delist_are_separate(accounts, staff, business_login):
    names, password, stations = accounts
    profiles = []
    users = []
    clients = []
    standard_id = None

    def login(name):
        c = TestClient(app, client=("rating-grant-" + secrets.token_hex(8), 1))
        clients.append(c)
        r = business_login(c, name, password)
        assert r.status_code == 200
        return c, {"x-csrf-token": r.json()["csrf_token"]}

    root, rh = login(staff[0][0])
    with SessionLocal.begin() as db:
        company = db.scalar(select(User.operator_id).where(User.username == names[0]))
        db.get(Station, stations[0]).status = "operating"
    try:

        def actor(cap, base="operator"):
            p = root.post(
                "/api/v1/roles",
                headers=rh,
                json={
                    "name": "rating-" + secrets.token_hex(8),
                    "base_role": base,
                    "permissions": ["stations.read", "ratings.read", "ratings." + cap],
                },
            )
            assert p.status_code == 201
            profiles.append(p.json()["id"])
            name = "rating-grant-" + secrets.token_hex(8)
            u = root.post(
                "/api/v1/accounts",
                headers=rh,
                json={
                    "username": name,
                    "password": password,
                    "role_profile_id": profiles[-1],
                    "operator_id": company if base == "operator" else None,
                },
            )
            assert u.status_code == 201
            users.append(u.json()["id"])
            return login(name)

        creator, ch = actor("create")
        editor, eh = actor("edit")
        material, mh = actor("materials")
        submitter, sh = actor("submit")
        withdrawer, wh = actor("withdraw")
        reviewer, vh = actor("review", "association")
        publisher, ph = actor("publish", "association")
        delister, dh = actor("delist", "association")
        maker, kh = actor("standards", "association")
        rules = {
            "code": "fine-" + secrets.token_hex(8),
            "version": 1,
            "name": "独立评价动作演示",
            "indicators": [
                {
                    "code": "service",
                    "name": "服务",
                    "weight": "1",
                    "max_score": "100",
                    "source": "manual",
                }
            ],
            "thresholds": [{"minimum": "60", "stars": 1}, {"minimum": "80", "stars": 3}],
        }
        for indicator in rules["indicators"]:
            indicator.update(
                enabled=True,
                description="独立动作验证分档",
                effective_from="2026-01-01",
                field_name="expert_score",
                bands=[{"lower": "0", "upper": None, "score": "95", "label": "已核验人工档位"}],
            )
        assert reviewer.post("/api/v1/ratings/standards", headers=vh, json=rules).status_code == 403
        r = maker.post("/api/v1/ratings/standards", headers=kh, json=rules)
        assert r.status_code == 201
        standard_id = r.json()["id"]
        body = {
            "station_id": stations[0],
            "standard_id": standard_id,
            "scores": {"service": "90"},
            "evidence_note": "演示依据",
        }
        assert editor.post("/api/v1/ratings/applications", headers=eh, json=body).status_code == 403
        created = creator.post("/api/v1/ratings/applications", headers=ch, json=body)
        assert created.status_code == 201
        aid = created.json()["id"]
        url = f"/api/v1/ratings/applications/{aid}"
        edit = {"version": 1, "scores": {"service": "85"}, "evidence_note": "补充依据"}
        assert creator.patch(url, headers=ch, json=edit).status_code == 403
        assert editor.patch(url, headers=eh, json=edit).status_code == 200
        document = {"file": ("proof.pdf", b"%PDF-1.4\nfixture", "application/pdf")}
        assert (
            editor.post(
                url + "/attachments",
                headers=eh,
                files=document,
                data={"category": "service_record"},
            ).status_code
            == 403
        )
        uploaded = material.post(
            url + "/attachments", headers=mh, files=document, data={"category": "service_record"}
        )
        assert uploaded.status_code == 201
        mid = uploaded.json()["id"]

        def action(c, h, verb, version, **extra):
            return c.post(
                url + "/actions",
                headers=h,
                json={"action": verb, "version": version, "comment": "动作隔离核验", **extra},
            )

        assert action(creator, ch, "submit", 2).status_code == 403
        assert action(submitter, sh, "submit", 2).status_code == 200
        assert material.delete(url + f"/attachments/{mid}", headers=mh).status_code == 409
        assert action(publisher, ph, "review", 3, scores={"service": "0"}).status_code == 403
        assert action(reviewer, vh, "review", 3, scores={"service": "95"}).status_code == 200
        assert (
            action(
                reviewer,
                vh,
                "publish",
                4,
                valid_until=(date.today() + timedelta(days=30)).isoformat(),
            ).status_code
            == 403
        )
        published = action(
            publisher,
            ph,
            "publish",
            4,
            valid_until=(date.today() + timedelta(days=30)).isoformat(),
            scores={"service": "0"},
        )
        assert published.status_code == 200 and published.json()["calculation"]["total"] == "95.00"
        assert action(publisher, ph, "delist", 5).status_code == 403
        assert action(delister, dh, "delist", 5).status_code == 200
        continuation = creator.post(
            "/api/v1/ratings/applications",
            headers=ch,
            json={**body, "kind": "reassessment", "parent_id": aid, "parent_version": 6},
        )
        assert continuation.status_code == 201
        url = f"/api/v1/ratings/applications/{continuation.json()['id']}"
        assert action(submitter, sh, "withdraw", 1).status_code == 403
        assert action(withdrawer, wh, "withdraw", 1).status_code == 200
        legacy = root.post(
            "/api/v1/roles",
            headers=rh,
            json={
                "name": "rating-legacy-" + secrets.token_hex(8),
                "base_role": "association",
                "permissions": ["stations.read", "ratings.read", "ratings.write"],
            },
        )
        assert legacy.status_code == 201
        profiles.append(legacy.json()["id"])
        assert set(legacy.json()["permissions"]) == {
            "stations.read",
            "ratings.read",
            "ratings.review",
            "ratings.publish",
            "ratings.delist",
            "ratings.standards",
        }
        assert (
            root.post(
                "/api/v1/roles",
                headers=rh,
                json={
                    "name": "bad-" + secrets.token_hex(8),
                    "base_role": "association",
                    "permissions": ["stations.read", "ratings.publish"],
                },
            ).status_code
            == 422
        )
    finally:
        for c in clients:
            c.close()
        with SessionLocal.begin() as db:
            if standard_id:
                apps = select(RatingApplication.id).where(
                    RatingApplication.standard_id == standard_id
                )
                for item in db.scalars(
                    select(RatingAttachment).where(RatingAttachment.application_id.in_(apps))
                ):
                    (Path(settings.storage_path).resolve() / item.storage_key).unlink(
                        missing_ok=True
                    )
                db.execute(
                    delete(RatingAttachment).where(RatingAttachment.application_id.in_(apps))
                )
                db.execute(delete(RatingAction).where(RatingAction.application_id.in_(apps)))
                db.execute(
                    delete(RatingApplication).where(RatingApplication.standard_id == standard_id)
                )
                db.execute(delete(RatingStandard).where(RatingStandard.id == standard_id))
            db.execute(delete(LoginSession).where(LoginSession.user_id.in_(users)))
            db.execute(delete(Audit).where(Audit.user_id.in_(users)))
            db.execute(delete(User).where(User.id.in_(users)))
            db.execute(delete(RoleProfile).where(RoleProfile.id.in_(profiles)))
