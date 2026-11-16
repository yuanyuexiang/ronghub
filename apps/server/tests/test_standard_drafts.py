import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import delete, func, select, text
from test_business_exports import login
from test_rating_configuration import standard_body
from test_subsidy_evidence import headers

from app.db import SessionLocal
from app.models import (
    Audit,
    RatingAction,
    RatingApplication,
    RatingAttachment,
    RatingStandard,
    RatingStandardDraft,
    RoleProfile,
    Station,
    User,
)

BASE = "/api/v1/ratings/standard-drafts"
STANDARDS = "/api/v1/ratings/standards"


def draft_data():
    value = standard_body()
    value["version"] = str(value["version"])
    for threshold in value["thresholds"]:
        threshold["stars"] = str(threshold["stars"])
    return value


def post(client, path, data):
    return client.post(path, headers=headers(client), json=data)


@pytest.fixture
def management(accounts, staff):
    owner = login(accounts[0][0], accounts[1])
    association = login(staff[0][1], staff[1])
    regulator = login(staff[0][0], staff[1])
    with SessionLocal.begin() as db:
        uid = db.scalar(select(User.id).where(User.username == staff[0][1]))
        db.get(Station, accounts[2][0]).status = "operating"
    yield owner, association, regulator, accounts[2][0], uid
    for client in [owner, association, regulator]:
        client.close()
    with SessionLocal.begin() as db:
        ids = set(
            db.scalars(
                select(Audit.object_id).where(
                    Audit.user_id == uid, Audit.action == "ratings.standard_create"
                )
            )
        )
        db.execute(delete(RatingStandardDraft).where(RatingStandardDraft.created_by == uid))
        aids = select(RatingApplication.id).where(RatingApplication.standard_id.in_(ids))
        db.execute(delete(RatingAttachment).where(RatingAttachment.application_id.in_(aids)))
        db.execute(delete(RatingAction).where(RatingAction.application_id.in_(aids)))
        db.execute(delete(RatingApplication).where(RatingApplication.standard_id.in_(ids)))
        db.execute(delete(RatingStandard).where(RatingStandard.id.in_(ids)))


def create(client, data=None):
    response = post(client, BASE, {"data": {} if data is None else data})
    assert response.status_code == 201, response.text
    return response.json()


def finalize(client, draft):
    return post(client, BASE + f"/{draft['id']}/finalize", {"version": draft["version"]})


def test_partial_draft_restores_then_finalizes_once(management):
    _owner, assoc, _reg, _sid, _uid = management
    draft = create(
        assoc, {"name": "尚未完整的指标配置", "indicators": [{"name": "待配置指标", "weight": "-"}]}
    )
    path = BASE + f"/{draft['id']}"
    assert draft["status"] == "draft" and draft["version"] == 1
    assert assoc.get(path).json()["data"] == draft["data"]
    failed = finalize(assoc, draft)
    assert failed.status_code == 422 and "草稿尚未完整" in failed.json()["detail"]
    assert assoc.get(path).json()["version"] == 1
    complete = draft_data()
    response = assoc.patch(path, headers=headers(assoc), json={"version": 1, "data": complete})
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["version"] == 2
    success = finalize(assoc, draft)
    assert success.status_code == 200, success.text
    result = success.json()
    assert result["draft"]["status"] == "published" and result["draft"]["version"] == 3
    assert result["draft"]["published_standard_id"] == result["standard"]["id"]
    assert result["standard"]["revision"] == 1 and not result["standard"]["archived"]
    assert finalize(assoc, draft).status_code == 409
    assert (
        assoc.patch(path, headers=headers(assoc), json={"version": 3, "data": complete}).status_code
        == 409
    )
    assert assoc.delete(path + "?version=3", headers=headers(assoc)).status_code == 409
    assert assoc.get(BASE).json()["total"] == 0
    assert assoc.get(BASE + "?status=published").json()["total"] == 1


def test_stale_update_and_delete_preserve_draft_data_and_history(management):
    _owner, assoc, _reg, _sid, uid = management
    draft = create(assoc, {"name": "原始草稿"})
    path = BASE + f"/{draft['id']}"
    update = assoc.patch(
        path, headers=headers(assoc), json={"version": 1, "data": {"name": "已保存输入"}}
    )
    assert update.status_code == 200
    assert (
        assoc.patch(
            path, headers=headers(assoc), json={"version": 1, "data": {"name": "过期覆盖"}}
        ).status_code
        == 409
    )
    assert assoc.delete(path + "?version=1", headers=headers(assoc)).status_code == 409
    removed = assoc.delete(path + "?version=2", headers=headers(assoc))
    assert removed.status_code == 200
    assert removed.json()["status"] == "deleted" and removed.json()["data"]["name"] == "已保存输入"
    assert assoc.get(path).json() == removed.json()
    assert (
        assoc.get(BASE).json()["total"] == 0
        and assoc.get(BASE + "?status=deleted").json()["total"] == 1
    )
    with SessionLocal() as db:
        events = list(
            db.scalars(
                select(Audit)
                .where(Audit.user_id == uid, Audit.action.like("ratings.standard_draft_%"))
                .order_by(Audit.id)
            )
        )
        assert [row.action for row in events] == [
            "ratings.standard_draft_create",
            "ratings.standard_draft_edit",
            "ratings.standard_draft_delete",
        ]
        assert all("已保存输入" not in str(row.detail) for row in events)


def test_duplicate_code_version_keeps_losing_draft_editable(management):
    _owner, assoc, _reg, _sid, _uid = management
    data = draft_data()
    drafts = [create(assoc, data) for _ in range(2)]
    results = [finalize(assoc, draft) for draft in drafts]
    assert [result.status_code for result in results] == [200, 409]
    losing = assoc.get(BASE + f"/{drafts[1]['id']}").json()
    assert losing["status"] == "draft" and losing["version"] == 1 and losing["data"] == data


def test_standards_availability_and_reference_safe_archive(management):
    owner, assoc, _reg, sid, _uid = management
    result = finalize(assoc, create(assoc, draft_data())).json()
    standard = result["standard"]
    path = STANDARDS + f"/{standard['id']}"
    disable = post(assoc, path + "/availability", {"revision": 1, "enabled": False})
    assert disable.status_code == 200 and disable.json()["revision"] == 2
    payload = {"station_id": sid, "standard_id": standard["id"], "scores": {"service": "80"}}
    assert post(owner, "/api/v1/ratings/applications", payload).status_code == 409
    assert post(assoc, path + "/availability", {"revision": 1, "enabled": True}).status_code == 409
    assert post(assoc, path + "/availability", {"revision": 2, "enabled": True}).status_code == 200
    application = post(owner, "/api/v1/ratings/applications", payload)
    assert application.status_code == 201
    aid = application.json()["id"]
    before = owner.get(f"/api/v1/ratings/applications/{aid}").json()
    assert assoc.delete(path + "?revision=3", headers=headers(assoc)).status_code == 409
    assert post(assoc, path + "/availability", {"revision": 3, "enabled": False}).status_code == 200
    assert (
        post(
            owner, f"/api/v1/ratings/applications/{aid}/actions", {"action": "submit", "version": 1}
        ).status_code
        == 200
    )
    assert (
        owner.get(f"/api/v1/ratings/applications/{aid}").json()["rule_snapshot"]
        == before["rule_snapshot"]
    )
    unused = finalize(assoc, create(assoc, draft_data())).json()["standard"]
    deleted = assoc.delete(STANDARDS + f"/{unused['id']}?revision=1", headers=headers(assoc))
    assert (
        deleted.status_code == 200
        and deleted.json()["archived"]
        and deleted.json()["revision"] == 2
    )
    assert unused["id"] not in [row["id"] for row in owner.get(STANDARDS).json()["items"]]
    assert (
        post(
            owner, "/api/v1/ratings/applications", {**payload, "standard_id": unused["id"]}
        ).status_code
        == 409
    )
    with SessionLocal() as db:
        assert db.get(RatingStandard, unused["id"]).rules


def test_manager_role_capability_global_scope_and_demo_gate(management, monkeypatch):
    owner, assoc, reg, _sid, uid = management
    from app.config import settings

    for client in [owner, reg]:
        assert client.get(BASE).status_code == 403
        assert post(client, BASE, {"data": {}}).status_code == 403
    profile_id = None
    try:
        with SessionLocal.begin() as db:
            profile = RoleProfile(
                name="draft-read-" + secrets.token_hex(8),
                base_role="association",
                permissions=["ratings.read"],
            )
            db.add(profile)
            db.flush()
            profile_id = profile.id
            db.get(User, uid).role_profile_id = profile_id
        assert (
            assoc.get(BASE).status_code == 403
            and post(assoc, BASE, {"data": {}}).status_code == 403
        )
        with SessionLocal.begin() as db:
            db.get(User, uid).role_profile_id = None
            db.get(User, uid).allowed_districts = []
        assert assoc.get(BASE).status_code == 403
        with SessionLocal.begin() as db:
            db.get(User, uid).allowed_districts = None
        monkeypatch.setattr(settings, "demo_mode", False)
        assert post(assoc, BASE, {"data": {}}).status_code == 409
    finally:
        with SessionLocal.begin() as db:
            db.get(User, uid).role_profile_id = None
            db.get(User, uid).allowed_districts = None
            if profile_id:
                db.delete(db.get(RoleProfile, profile_id))


@pytest.mark.parametrize(
    "data",
    [
        {"name": "\x00"},
        {"unknown": 1},
        {"version": 1},
        {"indicators": [{}] * 51},
        {"categories": [{}] * 21},
        {"thresholds": [{}] * 6},
        {"indicators": [{"bands": [{}] * 21}]},
        {"indicators": [{"field_name": "arbitrary_sql"}]},
        {"name": "x" * 151},
    ],
)
def test_draft_shape_bounds(data, management):
    _owner, assoc, _reg, _sid, _uid = management
    assert post(assoc, BASE, {"data": data}).status_code == 422


def test_projection_failures_rollback_draft_and_finalization(management, monkeypatch):
    _owner, assoc, _reg, _sid, _uid = management
    from app.routers import rating_standards as route

    draft = create(assoc, draft_data())
    path = BASE + f"/{draft['id']}"
    validator = route.RatingDraftDetail.model_validate

    def fail(*args, **kwargs):
        raise ValidationError.from_exception_data("RatingDraftDetail", [])

    from pydantic import ValidationError

    monkeypatch.setattr(route.RatingDraftDetail, "model_validate", fail)
    with pytest.raises(ValidationError):
        assoc.patch(path, headers=headers(assoc), json={"version": 1, "data": {"name": "不应保存"}})
    monkeypatch.setattr(route.RatingDraftDetail, "model_validate", validator)
    assert assoc.get(path).json()["version"] == 1
    monkeypatch.setattr(route.Finalized, "model_validate", fail)
    with pytest.raises(ValidationError):
        finalize(assoc, draft)
    assert assoc.get(path).json()["status"] == "draft"
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(RatingStandard)
                .where(RatingStandard.code == draft["data"]["code"])
            )
            == 0
        )


def test_archive_waits_for_actual_new_application_reference(management, monkeypatch):
    owner, assoc, _reg, sid, _uid = management
    from app import rating_configuration

    standard = finalize(assoc, create(assoc, draft_data())).json()["standard"]
    captured, release = Event(), Event()
    original = rating_configuration.capture

    def held(*args):
        result = original(*args)
        captured.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(rating_configuration, "capture", held)
    with ThreadPoolExecutor(max_workers=2) as executor:
        creation = executor.submit(
            post,
            owner,
            "/api/v1/ratings/applications",
            {"station_id": sid, "standard_id": standard["id"], "scores": {"service": "80"}},
        )
        assert captured.wait(10)
        deletion = executor.submit(
            lambda: assoc.delete(
                STANDARDS + f"/{standard['id']}?revision=1", headers=headers(assoc)
            )
        )
        try:
            deadline = time.monotonic() + 5
            blocked = False
            while time.monotonic() < deadline:
                with SessionLocal() as db:
                    blocked = bool(
                        db.scalar(
                            text(
                                "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE '%rating_standards%' AND query NOT LIKE '%pg_stat_activity%'"
                            )
                        )
                    )
                if blocked:
                    break
                time.sleep(0.01)
            assert blocked and not deletion.done()
        finally:
            release.set()
        assert creation.result().status_code == 201
        assert deletion.result().status_code == 409
    with SessionLocal() as db:
        assert db.get(RatingStandard, standard["id"]).archived is False


def test_archived_configuration_is_recoverable_only_for_manager(management):
    owner, assoc, reg, _sid, _uid = management
    standard = finalize(assoc, create(assoc, draft_data())).json()["standard"]
    path = STANDARDS + f"/{standard['id']}"
    assert assoc.delete(path + "?revision=1", headers=headers(assoc)).status_code == 200
    assert assoc.get(path).json()["archived"] is True
    assert standard["id"] in [
        item["id"] for item in assoc.get(STANDARDS + "?include_archived=true").json()["items"]
    ]
    for client in [owner, reg]:
        assert client.get(path).status_code == 403
        assert client.get(STANDARDS + "?include_archived=true").status_code == 403
    with SessionLocal() as db:
        audit = db.scalar(
            select(Audit).where(
                Audit.action == "ratings.standard_archive", Audit.object_id == standard["id"]
            )
        )
        assert audit.detail["before"] == {"enabled": True, "archived": False, "revision": 1}
        assert audit.detail["after"] == {"enabled": False, "archived": True, "revision": 2}
    from app.routers.audits import safe_details

    assert safe_details(audit.detail) == audit.detail


def test_availability_and_archive_projection_failure_do_not_commit(management, monkeypatch):
    _owner, assoc, _reg, _sid, _uid = management
    standard = finalize(assoc, create(assoc, draft_data())).json()["standard"]
    from pydantic import ValidationError

    from app.routers import rating_standards as route

    def fail(*args, **kwargs):
        raise ValidationError.from_exception_data("RatingStandardResponse", [])

    monkeypatch.setattr(route.RatingStandardResponse, "model_validate", fail)
    path = STANDARDS + f"/{standard['id']}"
    with pytest.raises(ValidationError):
        post(assoc, path + "/availability", {"revision": 1, "enabled": False})
    with pytest.raises(ValidationError):
        assoc.delete(path + "?revision=1", headers=headers(assoc))
    with SessionLocal() as db:
        row = db.get(RatingStandard, standard["id"])
        assert row.enabled and not row.archived and row.revision == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(Audit)
                .where(
                    Audit.object_id == row.id,
                    Audit.action.in_(["ratings.standard_archive", "ratings.standard_availability"]),
                )
            )
            == 0
        )


def test_simultaneous_finalization_has_one_immutable_winner(management):
    _owner, assoc, _reg, _sid, _uid = management
    data = draft_data()
    drafts = [create(assoc, data) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda draft: finalize(assoc, draft), drafts))
    assert sorted(response.status_code for response in results) == [200, 409]
    rows = [assoc.get(BASE + f"/{draft['id']}").json() for draft in drafts]
    assert sorted(row["status"] for row in rows) == ["draft", "published"]
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(RatingStandard)
                .where(RatingStandard.code == data["code"])
            )
            == 1
        )


def test_paged_literal_search_preserves_draft_rows(management):
    _owner, assoc, _reg, _sid, _uid = management
    for index in range(3):
        create(assoc, {"code": f"literal%_{index}", "name": "分页草稿"})
    response = assoc.get(BASE, params={"q": "%_", "page_size": 2, "page": 2})
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 3 and len(page["items"]) == 1 and page["page"] == 2
    assert assoc.get(BASE, params={"q": "no-matching"}).json()["total"] == 0
    assert assoc.get(BASE + "?status=invalid").status_code == 422


def test_draft_utf8_byte_limit_rejects_structurally_valid_oversize_data(management):
    _owner, assoc, _reg, _sid, uid = management
    response = post(
        assoc, BASE, {"data": {"indicators": [{"description": "测" * 2000} for _ in range(50)]}}
    )
    assert response.status_code == 422
    assert any("256KiB" in row["msg"] for row in response.json()["detail"])
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(RatingStandardDraft)
                .where(RatingStandardDraft.created_by == uid)
            )
            == 0
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"version": 2147483648},
        {"version": True},
        {"code": "bad\x00code"},
        {"name": " "},
        {"archived": True},
    ],
)
def test_final_standard_input_is_bounded_before_database(management, changes):
    _owner, assoc, _reg, _sid, _uid = management
    payload = standard_body()
    payload.update(changes)
    assert post(assoc, STANDARDS, payload).status_code == 422


def test_oversize_business_version_can_remain_draft_but_cannot_finalize(management):
    _owner, assoc, _reg, _sid, _uid = management
    data = draft_data()
    data["version"] = "9999999999"
    draft = create(assoc, data)
    result = finalize(assoc, draft)
    assert result.status_code == 422
    unchanged = assoc.get(BASE + f"/{draft['id']}").json()
    assert (
        unchanged["status"] == "draft"
        and unchanged["version"] == 1
        and unchanged["data"]["version"] == "9999999999"
    )
