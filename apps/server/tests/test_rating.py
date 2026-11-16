import secrets
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

from app import rating, rating_results
from app.db import SessionLocal
from app.models import RatingAction, RatingApplication, RatingStandard, Station, User

RULES = {
    "indicators": [
        {"code": "service", "name": "服务", "weight": "60", "max_score": "100", "source": "manual"},
        {
            "code": "runtime",
            "name": "运行数据",
            "weight": "40",
            "max_score": "100",
            "source": "runtime",
        },
    ],
    "thresholds": [
        {"minimum": "60", "stars": 1},
        {"minimum": "80", "stars": 3},
        {"minimum": "95", "stars": 5},
    ],
}


@pytest.fixture()
def rating_fixture(accounts):
    names, _, sids = accounts
    with SessionLocal.begin() as db:
        uid = db.scalar(select(User.id).where(User.username == names[0]))
        db.get(Station, sids[0]).status = "operating"
        standard = RatingStandard(
            code="test-" + secrets.token_hex(8), version=1, name="示例评价", rules=RULES
        )
        db.add(standard)
        db.flush()
        sid = standard.id
    yield uid, sids[0], sid
    with SessionLocal.begin() as db:
        aids = select(RatingApplication.id).where(RatingApplication.standard_id == sid)
        db.execute(delete(RatingAction).where(RatingAction.application_id.in_(aids)))
        db.execute(delete(RatingApplication).where(RatingApplication.standard_id == sid))
        db.execute(delete(RatingStandard).where(RatingStandard.id == sid))


def test_missing_runtime_review_version_and_publish(rating_fixture):
    uid, station, sid = rating_fixture
    with SessionLocal.begin() as db:
        user = db.get(User, uid)
        association = SimpleNamespace(id=uid, role="association")
        a = rating.create(db, user, station, sid, {"service": "90", "runtime": "100"}, "自评材料")
        assert a.calculation["total"] is None
        assert a.initial_scores["runtime"] is None
        assert a.calculation["missing"] == ["runtime"]
        rating.act(db, user, a.id, "submit", 1, "", {})
        with pytest.raises(HTTPException) as e:
            rating.act(db, association, a.id, "review", 2, "尚缺数据", {})
        assert e.value.status_code == 422
        with pytest.raises(HTTPException) as e:
            rating.act(db, user, a.id, "review", 2, "越权", {"runtime": "80"})
        assert e.value.status_code == 403
        rating.act(db, association, a.id, "review", 2, "核验证据后补分", {"runtime": "80"})
        assert Decimal(a.calculation["total"]) == Decimal("86.00")
        assert a.calculation["stars"] == 3
        assert a.initial_scores["runtime"] is None
        with pytest.raises(HTTPException) as e:
            rating.act(db, association, a.id, "publish", 2, "旧版本", {})
        assert e.value.status_code == 409
        rating.act(
            db,
            association,
            a.id,
            "publish",
            3,
            "同意公示",
            {},
            rating_results.local_day() + timedelta(days=30),
        )
        assert a.status == "published"
        with pytest.raises(HTTPException) as e:
            rating.act(db, association, a.id, "review", 4, "已公示不能覆盖", {"runtime": "100"})
        assert e.value.status_code == 409
        rating.act(db, association, a.id, "delist", 4, "摘牌演示", {})
        assert a.status == "delisted"
        assert a.rule_snapshot["version"] == 1


def test_rating_bounds_and_unknown_indicators():
    with pytest.raises(HTTPException):
        rating.calculate(RULES, {"service": "101", "runtime": "80"})
    with pytest.raises(HTTPException):
        rating.calculate(RULES, {"service": "NaN", "runtime": "80"})
    with pytest.raises(HTTPException):
        rating.calculate(RULES, {"service": "80", "unknown": "80"})
    assert rating.calculate(RULES, {"service": "95", "runtime": "95"})["stars"] == 5


def test_category_normalization_and_missing():
    rules = {
        "categories": [
            {"code": "a", "name": "设施", "weight": "35"},
            {"code": "b", "name": "服务", "weight": "65"},
        ],
        "indicators": [
            {
                "code": "x",
                "name": "x",
                "category": "a",
                "weight": "10",
                "max_score": "50",
                "source": "manual",
            },
            {
                "code": "y",
                "name": "y",
                "category": "a",
                "weight": "90",
                "max_score": "100",
                "source": "manual",
            },
            {
                "code": "z",
                "name": "z",
                "category": "b",
                "weight": "1",
                "max_score": "100",
                "source": "runtime",
            },
        ],
        "thresholds": [{"minimum": "50", "stars": 1}],
    }
    result = rating.calculate(rules, {"x": "50", "y": "0", "z": "80"})
    assert result["total"] == "55.50"
    assert [g["score"] for g in result["categories"]] == ["10.00", "80.00"]
    assert result["formula"] == "category_weighted"
    result = rating.calculate(rules, {"x": "50", "y": "0"})
    assert result["total"] is None and result["stars"] is None
    assert result["categories"][1]["missing"] == ["z"]


def test_materials_limits_scope_and_frozen_history(rating_fixture, accounts, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app
    from app.models import RatingAttachment

    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    uid, station, sid = rating_fixture
    names, password, _ = accounts
    with SessionLocal.begin() as db:
        db.get(Station, station).status = "access_approved"
        a = rating.create(db, db.get(User, uid), station, sid, {"service": "90"}, "材料测试")
        aid = a.id
    try:
        with (
            TestClient(app, client=("rating-" + secrets.token_hex(6), 1)) as owner,
            TestClient(app, client=("rating-other-" + secrets.token_hex(6), 1)) as other,
        ):
            r = owner.post("/api/v1/auth/login", json={"username": names[0], "password": password})
            assert r.status_code == 200
            headers = {"x-csrf-token": r.json()["csrf_token"]}
            assert (
                other.post(
                    "/api/v1/auth/login", json={"username": names[1], "password": password}
                ).status_code
                == 200
            )
            base = f"/api/v1/ratings/applications/{aid}"
            url = base + "/attachments"

            def upload(name, data):
                return owner.post(
                    url,
                    headers=headers,
                    data={"category": "site_plan"},
                    files={"file": (name, data, "application/octet-stream")},
                )

            assert upload("wrong.docx", b"PK\x03\x04example").status_code == 415
            assert not list(tmp_path.iterdir())
            first = upload("site.zip", b"PK\x03\x04" + b"x" * (11 * 1024 * 1024))
            assert first.status_code == 201
            mid = first.json()["id"]
            assert (
                upload("excess.zip", b"PK\x03\x04" + b"x" * (10 * 1024 * 1024)).status_code == 413
            )
            assert len(list(tmp_path.iterdir())) == 1
            assert other.get(url).status_code == 404
            assert other.get(f"{url}/{mid}/download").status_code == 404
            assert owner.get(f"{url}/{mid}/download").content.startswith(b"PK")
            r = owner.post(
                base + "/actions", headers=headers, json={"action": "submit", "version": 1}
            )
            assert r.status_code == 200
            assert owner.get(url).json()["items"][0]["submitted_version"] == 2
            assert owner.delete(f"{url}/{mid}", headers=headers).status_code == 409
            with SessionLocal.begin() as db:
                a = rating.access(db, db.get(User, uid), aid)
                rating.act(
                    db,
                    SimpleNamespace(id=uid, role="association"),
                    aid,
                    "reject",
                    a.version,
                    "补充材料",
                    {},
                )
            assert owner.delete(f"{url}/{mid}", headers=headers).status_code == 409
            extra = upload("extra.pdf", b"%PDF-1.4 example")
            assert extra.status_code == 201
            assert owner.delete(f"{url}/{extra.json()['id']}", headers=headers).status_code == 200
            history = owner.get(base).json()["history"]
            assert len(history[1]["payload"]["materials"][0]["sha256"]) == 64
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(RatingAttachment).where(RatingAttachment.application_id == aid))


def test_rejected_edit_preserves_initial_and_requires_version(rating_fixture):
    uid, station, sid = rating_fixture
    with SessionLocal.begin() as db:
        owner = db.get(User, uid)
        reviewer = SimpleNamespace(id=uid, role="association")
        a = rating.create(db, owner, station, sid, {"service": "90"}, "初次")
        rating.act(db, owner, a.id, "submit", 1, "", {})
        rating.act(db, reviewer, a.id, "reject", 2, "补充", {})
        with pytest.raises(HTTPException) as e:
            rating.edit(db, owner, a.id, 2, {"service": "70"}, "迟到修改")
        assert e.value.status_code == 409
        rating.edit(db, owner, a.id, 3, {"service": "70", "runtime": "100"}, "修改")
        assert a.initial_scores["service"] == "90"
        assert a.current_scores == {"service": "70", "runtime": None}
        rating.act(db, owner, a.id, "submit", 4, "", {})
        assert a.status == "submitted"
        with pytest.raises(HTTPException):
            rating.edit(db, owner, a.id, 5, {"service": "80"}, "已提交不可改")


def test_standard_category_validation():
    from pydantic import ValidationError

    from app.routers.rating import StandardInput

    body = {"code": "test", "name": "test", "version": 1, **RULES}
    body["categories"] = [{"code": "service", "name": "服务", "weight": "100"}]
    with pytest.raises(ValidationError):
        StandardInput.model_validate(body)
    body["indicators"] = [
        {
            **i,
            "enabled": True,
            "category": "service",
            "description": "校验样本",
            "effective_from": "2026-01-01",
            "field_name": "expert_score" if i["source"] == "manual" else "station_power_kw",
            "bands": [{"lower": "0", "upper": None, "score": "100", "label": "样本"}],
        }
        for i in RULES["indicators"]
    ]
    assert StandardInput.model_validate(body).categories[0].code == "service"
    body["categories"].append({"code": "empty", "name": "空分类", "weight": "1"})
    with pytest.raises(ValidationError):
        StandardInput.model_validate(body)


def published_rating(db, uid, station, sid, days=30, note="首次证据"):
    owner = db.get(User, uid)
    reviewer = SimpleNamespace(id=uid, role="association")
    a = rating.create(db, owner, station, sid, {"service": "90"}, note)
    rating.act(db, owner, a.id, "submit", 1, "", {})
    rating.act(db, reviewer, a.id, "review", 2, "核验运行数据证据", {"runtime": "80"})
    rating.act(
        db,
        reviewer,
        a.id,
        "publish",
        3,
        "公示依据",
        {},
        rating_results.local_day() + timedelta(days=days),
    )
    return a


def test_appeal_replaces_result_only_after_publication(rating_fixture):
    uid, station, sid = rating_fixture
    with SessionLocal.begin() as db:
        owner = db.get(User, uid)
        reviewer = SimpleNamespace(id=uid, role="association")
        original = published_rating(db, uid, station, sid)
        snapshot = original.report_snapshot
        with pytest.raises(HTTPException):
            rating.create(db, owner, station, sid, {}, "重复初次")
        with pytest.raises(HTTPException):
            rating.create(
                db,
                owner,
                station,
                sid,
                {},
                "提前到期复评",
                "renewal",
                original.id,
                original.version,
            )
        appeal = rating.create(
            db,
            owner,
            station,
            sid,
            {"service": "100"},
            "现场证据有遗漏",
            "appeal",
            original.id,
            original.version,
        )
        assert appeal.before_result["stars"] == 3
        assert rating_results.latest(db, station).id == original.id
        rating.act(db, owner, appeal.id, "submit", 1, "", {})
        rating.act(db, reviewer, appeal.id, "review", 2, "补核验", {"runtime": "100"})
        with pytest.raises(HTTPException) as error:
            rating.act(db, reviewer, appeal.id, "publish", 3, "缺有效期", {})
        assert error.value.status_code == 422
        assert rating_results.latest(db, station).id == original.id
        rating.act(
            db,
            reviewer,
            appeal.id,
            "publish",
            3,
            "申诉复核后公示",
            {},
            rating_results.local_day() + timedelta(days=60),
        )
        assert original.status == "superseded"
        assert original.report_snapshot == snapshot
        assert rating_results.latest(db, station).id == appeal.id
        assert rating_results.effective(appeal)["effective_stars"] == 5
        assert appeal.report_snapshot["before_result"]["id"] == original.id
        rating.act(db, reviewer, appeal.id, "delist", 4, "不再符合要求", {})
        assert rating_results.effective(appeal)["effective_stars"] == 0
        assert not rating_results.effective(appeal)["show_association_mark"]


def test_appeal_detects_changed_original_and_expiry_boundary(rating_fixture):
    uid, station, sid = rating_fixture
    with SessionLocal.begin() as db:
        owner = db.get(User, uid)
        reviewer = SimpleNamespace(id=uid, role="association")
        original = published_rating(db, uid, station, sid, days=0)
        assert rating_results.effective(original)["result_state"] == "valid"
        assert (
            rating_results.effective(
                original, today=rating_results.local_day() + timedelta(days=1)
            )["result_state"]
            == "expired"
        )
        appeal = rating.create(
            db, owner, station, sid, {}, "申诉", "appeal", original.id, original.version
        )
        rating.act(db, reviewer, original.id, "delist", 4, "原结果发生摘牌", {})
        with pytest.raises(HTTPException) as error:
            rating.act(db, owner, appeal.id, "submit", 1, "", {})
        assert error.value.status_code == 409
        assert appeal.status == "draft"
        assert rating_results.effective(original)["effective_stars"] == 0


def test_scoped_publication_search_and_frozen_report(rating_fixture, accounts, staff):
    from fastapi.testclient import TestClient

    from app.main import app

    uid, station, sid = rating_fixture
    names, password, _ = accounts
    staff_names, _ = staff
    with SessionLocal.begin() as db:
        a = published_rating(db, uid, station, sid, note="<script>snapshot-xss</script>")
        aid = a.id
        db.get(Station, station).name = "<script>alert(1)</script>"
        # Stored report retains the station name at publication.
    with (
        TestClient(app, client=("publication-" + secrets.token_hex(6), 1)) as own,
        TestClient(app, client=("publication-other-" + secrets.token_hex(6), 1)) as other,
        TestClient(app, client=("publication-reg-" + secrets.token_hex(6), 1)) as reg,
    ):
        for client, username in [(own, names[0]), (other, names[1]), (reg, staff_names[0])]:
            assert (
                client.post(
                    "/api/v1/auth/login", json={"username": username, "password": password}
                ).status_code
                == 200
            )
        url = "/api/v1/ratings/publications"
        result = own.get(url, params={"q": "<script>", "result_state": "valid", "stars": 3}).json()
        assert result["total"] == 1 and result["items"][0]["id"] == aid
        assert result["items"][0]["show_association_mark"] is True
        assert other.get(url, params={"q": "<script>"}).json()["total"] == 0
        report_url = f"/api/v1/ratings/applications/{aid}/report"
        assert other.get(report_url).status_code == 404
        report = reg.get(report_url)
        assert report.status_code == 200 and "attachment" in report.headers["content-disposition"]
        assert "隔离测试" in report.text and "alert(1)" not in report.text
        assert "演示评价报告" in report.text
        assert "&lt;script&gt;snapshot-xss&lt;/script&gt;" in report.text
        assert "<script>" not in report.text
        with SessionLocal.begin() as db:
            a = db.get(RatingApplication, aid)
            a.valid_until = rating_results.local_day() - timedelta(days=1)
        expired = own.get(
            url, params={"q": "<script>", "result_state": "expired", "stars": 0}
        ).json()
        assert expired["total"] == 1
        assert expired["items"][0]["effective_stars"] == 0
        assert not expired["items"][0]["show_association_mark"]
        with SessionLocal.begin() as db:
            a = db.get(RatingApplication, aid)
            a.valid_until = None
        unknown = own.get(url, params={"q": "<script>", "result_state": "validity_unknown"}).json()
        assert unknown["items"][0]["effective_stars"] is None
        with SessionLocal.begin() as db:
            staff_user = db.scalar(select(User).where(User.username == staff_names[0]))
            staff_user.allowed_districts = []
        assert reg.get(url).json()["total"] == 0
        assert reg.get(report_url).status_code == 404


def test_concurrent_followup_creation_has_one_active_application(rating_fixture):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    uid, station, sid = rating_fixture
    with SessionLocal.begin() as db:
        a = published_rating(db, uid, station, sid)
        parent_id, parent_version = a.id, a.version
    barrier = Barrier(2)

    def create_followup():
        try:
            with SessionLocal.begin() as db:
                owner = db.get(User, uid)
                barrier.wait(timeout=5)
                rating.create(
                    db,
                    owner,
                    station,
                    sid,
                    {"service": "90"},
                    "并发申诉",
                    "appeal",
                    parent_id,
                    parent_version,
                )
            return 201
        except HTTPException as e:
            return e.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(create_followup) for _ in range(2)]
        assert sorted(f.result(timeout=10) for f in futures) == [201, 409]


def test_expired_result_can_be_renewed_with_prior_snapshot(rating_fixture, monkeypatch):
    uid, station, sid = rating_fixture
    today = rating_results.local_day()
    with SessionLocal.begin() as db:
        original = published_rating(db, uid, station, sid, days=0)
        snapshot = original.report_snapshot
        monkeypatch.setattr(rating_results, "local_day", lambda: today + timedelta(days=1))
        owner = db.get(User, uid)
        renewal = rating.create(
            db,
            owner,
            station,
            sid,
            {"service": "90"},
            "到期重新评定",
            "renewal",
            original.id,
            original.version,
        )
        assert renewal.before_result["result_state"] == "expired"
        assert renewal.before_result["effective_stars"] == 0
        rating.act(db, owner, renewal.id, "submit", 1, "", {})
        reviewer = SimpleNamespace(id=uid, role="association")
        rating.act(db, reviewer, renewal.id, "review", 2, "核实复评材料", {"runtime": "80"})
        rating.act(
            db, reviewer, renewal.id, "publish", 3, "新有效期", {}, today + timedelta(days=90)
        )
        assert original.report_snapshot == snapshot and original.status == "superseded"
        assert rating_results.effective(renewal)["result_state"] == "valid"


def test_concurrent_publication_records_one_frozen_report(rating_fixture):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    uid, station, sid = rating_fixture
    with SessionLocal.begin() as db:
        a = rating.create(db, db.get(User, uid), station, sid, {"service": "90"}, "公示并发")
        rating.act(db, db.get(User, uid), a.id, "submit", 1, "", {})
        rating.act(
            db,
            SimpleNamespace(id=uid, role="association"),
            a.id,
            "review",
            2,
            "复核",
            {"runtime": "80"},
        )
        aid = a.id
    barrier = Barrier(2)

    def publish():
        try:
            with SessionLocal.begin() as db:
                barrier.wait(timeout=5)
                rating.act(
                    db,
                    SimpleNamespace(id=uid, role="association"),
                    aid,
                    "publish",
                    3,
                    "公示",
                    {},
                    rating_results.local_day() + timedelta(days=30),
                )
            return 200
        except HTTPException as e:
            return e.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(publish) for _ in range(2)]
        assert sorted(f.result(timeout=10) for f in futures) == [200, 409]
    with SessionLocal.begin() as db:
        a = db.get(RatingApplication, aid)
        assert a.report_snapshot["report_id"] == f"RATE-{aid}-V4"
        assert (
            len(
                list(
                    db.scalars(
                        select(RatingAction).where(
                            RatingAction.application_id == aid, RatingAction.action == "publish"
                        )
                    )
                )
            )
            == 1
        )


def test_score_audit_retains_missing_and_rolls_back_with_review(rating_fixture):
    from app.models import Audit

    uid, station, sid = rating_fixture
    association = SimpleNamespace(id=uid, role="association")
    with SessionLocal.begin() as db:
        user = db.get(User, uid)
        a = rating.create(db, user, station, sid, {"service": "90"}, "private-evidence-note")
        aid = a.id
        rating.act(db, user, aid, "submit", 1, "", {})
    with SessionLocal() as db:
        rating.act(db, association, aid, "review", 2, "private-review-note", {"runtime": "80"})
        db.flush()
        db.rollback()
    with SessionLocal.begin() as db:
        a = db.get(RatingApplication, aid)
        assert a.status == "submitted" and a.calculation["total"] is None
        assert (
            db.scalar(
                select(Audit.id).where(Audit.action == "ratings.review", Audit.object_id == aid)
            )
            is None
        )
        rating.act(db, association, aid, "review", 2, "private-review-note", {"runtime": "80"})
    with SessionLocal() as db:
        log = db.scalar(
            select(Audit).where(Audit.action == "ratings.review", Audit.object_id == aid)
        )
        assert log.detail["before"]["total_score"] is None
        assert log.detail["before"]["scores"]["runtime"] is None
        assert log.detail["after"]["scores"] == {"service": "90", "runtime": "80"}
        assert log.detail["after"]["total_score"] == "86.00"
        assert log.detail["after"]["stars"] == 3
        assert "private-" not in str(log.detail)
