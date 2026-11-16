# ruff: noqa: F811 - shared pytest fixture imports
import copy
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from test_rating_configuration import configuration, indicator, standard_body  # noqa:F401
from test_subsidy_evidence import headers

from app import rating
from app.db import SessionLocal
from app.models import Audit, RatingStandard
from app.rating_results import local_day


def sample(code, category, weight, score, enabled=True, **changes):
    return indicator(
        code=code,
        name=code,
        category=category,
        weight=str(weight),
        enabled=enabled,
        bands=[{"lower": "0", "upper": None, "score": str(score), "label": "explicit test band"}],
        **changes,
    )


def body():
    value = standard_body(
        [
            sample("a1", "a", 20, 100),
            sample("a2", "a", 30, 40),
            sample("off", "a", 999, 100, False),
            sample("b1", "b", 1, 100),
        ]
    )
    value["categories"] = [
        {"code": "a", "name": "A", "weight": "35"},
        {"code": "b", "name": "B", "weight": "65"},
    ]
    return value


def rules(value):
    from app.routers.rating import StandardInput

    data = StandardInput.model_validate(value).model_dump(mode="json")
    return {
        "configuration_version": 2,
        "indicator_status_version": 1,
        **{key: data[key] for key in ("indicators", "categories", "thresholds")},
    }


def test_disabled_is_excluded_from_both_denominator_and_missing():
    value = rules(body())
    scores = {"a1": "90", "a2": "20", "b1": "90"}
    result = rating.calculate(value, scores)
    assert result["total"] == "87.40" and result["missing"] == [] and result["excluded"] == ["off"]
    details = {row["code"]: row for row in result["details"]}
    assert details["a1"]["normalization_weight"] == "50"
    from decimal import Decimal

    assert {code: Decimal(row["effective_weight_percent"]) for code, row in details.items()} == {
        code: Decimal(value)
        for code, value in {"a1": "14", "a2": "21", "off": "0", "b1": "65"}.items()
    }
    assert details["off"]["score"] is None and details["off"]["enabled"] is False
    missing = rating.calculate(value, {"a2": "20", "b1": "90"})
    assert (
        missing["missing"] == ["a1"] and missing["total"] is None and missing["excluded"] == ["off"]
    )
    for supplied in ["0", "100"]:
        with pytest.raises(HTTPException) as error:
            rating.calculate(value, {**scores, "off": supplied})
        assert error.value.status_code == 422


@pytest.mark.parametrize(
    "changes", ["all_disabled", "empty_category", "missing", "null", "string", "number"]
)
def test_status_must_be_explicit_boolean_and_keep_categories(changes):
    from pydantic import ValidationError

    from app.routers.rating import StandardInput

    value = body()
    if changes == "all_disabled":
        for item in value["indicators"]:
            item["enabled"] = False
    elif changes == "empty_category":
        value["indicators"][-1]["enabled"] = False
    elif changes == "missing":
        value["indicators"][0].pop("enabled")
    else:
        value["indicators"][0]["enabled"] = {"null": None, "string": "false", "number": 1}[changes]
    with pytest.raises(ValidationError):
        StandardInput.model_validate(value)


def test_unknown_status_marker_is_not_legacy_fallback():
    value = rules(body())
    value["indicator_status_version"] = 99
    with pytest.raises(HTTPException) as error:
        rating.calculate(value, {})
    assert error.value.status_code == 409


def test_preview_is_role_scoped_decimal_and_does_not_write(configuration):
    owner, assoc, _sid, _create, _eid = configuration
    value = body()
    with SessionLocal() as db:
        before = (
            db.scalar(select(func.count()).select_from(RatingStandard)),
            db.scalar(
                select(func.count())
                .select_from(Audit)
                .where(Audit.action == "ratings.standard_create")
            ),
        )
    response = assoc.post("/api/v1/ratings/standards/preview", headers=headers(assoc), json=value)
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["excluded"] == ["off"] and {
        row["code"]: row["normalization_weight"] for row in plan["items"]
    } == {"a1": "50", "a2": "50", "off": "50", "b1": "1"}
    assert "total" not in plan and "stars" not in plan
    assert (
        owner.post(
            "/api/v1/ratings/standards/preview", headers=headers(owner), json=value
        ).status_code
        == 403
    )
    with SessionLocal() as db:
        after = (
            db.scalar(select(func.count()).select_from(RatingStandard)),
            db.scalar(
                select(func.count())
                .select_from(Audit)
                .where(Audit.action == "ratings.standard_create")
            ),
        )
    assert after == before


def test_future_disabled_does_not_delay_enabled_version_or_collect_a_score(configuration):
    owner, _assoc, sid, create, _eid = configuration
    value = body()
    value["indicators"][2]["effective_from"] = (local_day() + timedelta(days=30)).isoformat()
    standard = create(value)
    assert standard.status_code == 201, standard.text
    assert (
        standard.json()["activation_state"] == "effective"
        and standard.json()["effective_on"] == "2026-01-01"
    )
    response = owner.post(
        "/api/v1/ratings/applications",
        headers=headers(owner),
        json={
            "station_id": sid,
            "standard_id": standard.json()["id"],
            "scores": {"a1": "90", "a2": "20", "b1": "90"},
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["calculation"]["total"] == "87.40"


def test_new_version_changes_only_its_plan_and_older_application_keeps_original(configuration):
    owner, _assoc, sid, create, _eid = configuration
    value = body()
    first = create(value).json()
    response = owner.post(
        "/api/v1/ratings/applications",
        headers=headers(owner),
        json={
            "station_id": sid,
            "standard_id": first["id"],
            "scores": {"a1": "90", "a2": "20", "b1": "90"},
        },
    )
    assert response.status_code == 201
    path = f"/api/v1/ratings/applications/{response.json()['id']}"
    old = owner.get(path).json()
    revised = copy.deepcopy(value)
    revised["version"] = 2
    revised["indicators"][1]["enabled"] = False
    next_standard = create(revised)
    assert next_standard.status_code == 201
    assert rating.calculate(rules(revised), {"a1": "90", "b1": "90"})["total"] == "100.00"
    after = owner.get(path).json()
    assert (
        after["rule_snapshot"] == old["rule_snapshot"]
        and after["calculation"] == old["calculation"]
    )


def test_legacy_status_and_old_drafts_remain_unknown(configuration):
    owner, assoc, _sid, create, _eid = configuration
    value = standard_body()
    standard = create(value).json()
    with SessionLocal.begin() as db:
        row = db.get(RatingStandard, standard["id"])
        old = copy.deepcopy(row.rules)
        old.pop("indicator_status_version")
        for item in old["indicators"]:
            item.pop("enabled")
        row.rules = old
    legacy = owner.get(f"/api/v1/ratings/standards/{standard['id']}").json()
    assert (
        legacy["rules"]["indicator_status_version"] is None
        and legacy["rules"]["indicators"][0]["enabled"] is None
    )
    calculated = rating.calculate(old, {"service": "80"})
    assert calculated["excluded"] is None and "enabled" not in calculated["details"][0]
    # A pre-status draft requires an explicit choice, never a synthetic historical status.
    data = copy.deepcopy(value)
    data["version"] = "2"
    for item in data["indicators"]:
        item.pop("enabled")
    for item in data["thresholds"]:
        item["stars"] = str(item["stars"])
    draft = assoc.post(
        "/api/v1/ratings/standard-drafts", headers=headers(assoc), json={"data": data}
    ).json()
    try:
        assert draft["data"]["indicators"][0]["enabled"] is None
        result = assoc.post(
            f"/api/v1/ratings/standard-drafts/{draft['id']}/finalize",
            headers=headers(assoc),
            json={"version": 1},
        )
        assert result.status_code == 422
        assert assoc.get(f"/api/v1/ratings/standard-drafts/{draft['id']}").json()["version"] == 1
    finally:
        from sqlalchemy import delete

        from app.models import RatingStandardDraft

        with SessionLocal.begin() as db:
            db.execute(delete(RatingStandardDraft).where(RatingStandardDraft.id == draft["id"]))


def test_flat_configuration_exclusion_uses_its_actual_general_denominator():
    value = body()
    value["categories"] = []
    result = rating.calculate(rules(value), {"a1": "90", "a2": "20", "b1": "90"})
    assert all(row["category"] == "general" for row in result["details"])
    assert all(row["normalization_weight"] == "51" for row in result["details"])
