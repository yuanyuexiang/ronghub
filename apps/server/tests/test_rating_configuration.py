import secrets
from datetime import timedelta
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from test_business_exports import login
from test_subsidy_evidence import headers

from app.db import SessionLocal
from app.models import (
    Connector,
    Equipment,
    RatingAction,
    RatingApplication,
    RatingStandard,
    Station,
)
from app.rating_configuration import IndicatorInput, capture
from app.rating_results import local_day


def indicator(source="manual", field="expert_score", **changes):
    return {
        "enabled": True,
        "code": "service",
        "name": "服务",
        "weight": "100",
        "max_score": "100",
        "source": source,
        "field_name": field,
        "description": "按明确字段原值分档的演示配置",
        "effective_from": "2026-01-01",
        "category": "general",
        "bands": [
            {"lower": "0", "upper": "80", "score": "40", "label": "一般"},
            {"lower": "80", "upper": None, "score": "100", "label": "优秀"},
        ],
        **changes,
    }


def standard_body(indicators=None):
    return {
        "code": "configured-" + secrets.token_hex(8),
        "version": 1,
        "name": "字段阈值演示",
        "categories": [{"code": "general", "name": "综合", "weight": "100"}],
        "indicators": indicators or [indicator()],
        "thresholds": [{"minimum": "60", "stars": 1}, {"minimum": "90", "stars": 5}],
    }


@pytest.fixture
def configuration(accounts, staff):
    owner = login(accounts[0][0], accounts[1])
    association = login(staff[0][1], staff[1])
    sid = accounts[2][0]
    with SessionLocal.begin() as db:
        db.get(Station, sid).status = "operating"
        db.get(Station, sid).connectors = 999
        equipment = Equipment(
            station_id=sid, external_id=secrets.token_hex(10), name="实际设备", power_kw=10
        )
        db.add(equipment)
        db.flush()
        eid = equipment.id
        for _ in range(3):
            db.add(
                Connector(equipment_id=eid, external_id=secrets.token_hex(10), status="available")
            )
    standard_ids = []

    def create(body):
        response = association.post(
            "/api/v1/ratings/standards", headers=headers(association), json=body
        )
        if response.status_code == 201:
            standard_ids.append(response.json()["id"])
        return response

    yield owner, association, sid, create, eid
    owner.close()
    association.close()
    with SessionLocal.begin() as db:
        aids = select(RatingApplication.id).where(RatingApplication.standard_id.in_(standard_ids))
        db.execute(delete(RatingAction).where(RatingAction.application_id.in_(aids)))
        db.execute(delete(RatingApplication).where(RatingApplication.standard_id.in_(standard_ids)))
        db.execute(delete(RatingStandard).where(RatingStandard.id.in_(standard_ids)))
        db.execute(delete(Connector).where(Connector.equipment_id == eid))
        db.execute(delete(Equipment).where(Equipment.id == eid))


def apply(owner, sid, standard, scores):
    return owner.post(
        "/api/v1/ratings/applications",
        headers=headers(owner),
        json={
            "station_id": sid,
            "standard_id": standard,
            "scores": scores,
            "evidence_note": "阈值计算证据",
        },
    )


def test_interval_boundary_and_raw_mapped_values_are_distinct():
    from app.rating_configuration import mapped_values

    rules = {"configuration_version": 2, "indicators": [indicator()]}
    for raw, score, label in [
        ("0", "40", "一般"),
        ("79.999", "40", "一般"),
        ("80", "100", "优秀"),
        ("100", "100", "优秀"),
    ]:
        values, meta = mapped_values(rules, {"service": raw})
        assert values["service"] == score and meta["service"]["raw_value"] == raw
        assert meta["service"]["band_label"] == label
    for invalid in ["NaN", "Infinity", "-1", "100.001"]:
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            mapped_values(rules, {"service": invalid})


@pytest.mark.parametrize(
    "changes",
    [
        {"description": " "},
        {"effective_from": "2026-02-30"},
        {"field_name": "online_rate"},
        {"source": "runtime"},
        {"field_name": "station_power_kw"},
        {"unknown": 1},
        {"bands": [{"lower": "1", "upper": None, "score": "100", "label": "x"}]},
        {"bands": [{"lower": "0", "upper": "20", "score": "100", "label": "x"}]},
        {
            "bands": [
                {"lower": "0", "upper": "80", "score": "100", "label": "x"},
                {"lower": "79", "upper": None, "score": "10", "label": "y"},
            ]
        },
        {
            "bands": [
                {"lower": "0", "upper": "80", "score": "100", "label": "x"},
                {"lower": "81", "upper": None, "score": "10", "label": "y"},
            ]
        },
        {"bands": [{"lower": "0", "upper": None, "score": "101", "label": "x"}]},
        {"bands": [{"lower": "0", "upper": None, "score": "NaN", "label": "x"}]},
        {"bands": [{"lower": "0", "upper": None, "score": "50", "label": "\x00"}]},
    ],
)
def test_invalid_indicator_configuration_is_rejected(changes):
    with pytest.raises(ValidationError):
        IndicatorInput.model_validate(indicator(**changes))


def test_platform_capture_is_actual_inventory_frozen_not_declarations(configuration):
    owner, association, sid, create, eid = configuration
    payload = standard_body(
        [
            indicator(
                "runtime",
                "connector_count",
                bands=[
                    {"lower": "0", "upper": "3", "score": "20", "label": "不足3枪"},
                    {"lower": "3", "upper": None, "score": "90", "label": "至少3枪"},
                ],
            )
        ]
    )
    response = create(payload)
    assert response.status_code == 201, response.text
    standard = response.json()
    assert standard["activation_state"] == "effective" and standard["effective_on"] == "2026-01-01"
    rejected = apply(owner, sid, standard["id"], {"service": "100"})
    assert rejected.status_code == 422
    response = apply(owner, sid, standard["id"], {})
    assert response.status_code == 201, response.text
    aid = response.json()["id"]
    path = f"/api/v1/ratings/applications/{aid}"
    detail = owner.get(path).json()
    context = detail["rule_snapshot"]["evaluation_context"]
    assert (
        context["fields"]["connector_count"] == "3" and context["fields"]["equipment_count"] == "1"
    )
    assert context["completeness_verified"] is False
    assert detail["calculation"]["total"] == "90.00"
    row = detail["calculation"]["details"][0]
    assert row["raw_value"] == "3" and row["score"] == "90" and row["band_label"] == "至少3枪"
    with SessionLocal.begin() as db:
        db.add(Connector(equipment_id=eid, external_id=secrets.token_hex(8), status="available"))
    assert owner.get(path).json()["rule_snapshot"]["evaluation_context"] == context
    assert (
        owner.patch(
            path, headers=headers(owner), json={"version": 1, "scores": {"service": "100"}}
        ).status_code
        == 422
    )
    submitted = owner.post(
        path + "/actions", headers=headers(owner), json={"action": "submit", "version": 1}
    )
    assert submitted.status_code == 200, submitted.text
    assert (
        association.post(
            path + "/actions",
            headers=headers(association),
            json={
                "action": "review",
                "version": 2,
                "comment": "试图替代平台值",
                "scores": {"service": "100"},
            },
        ).status_code
        == 422
    )
    reviewed = association.post(
        path + "/actions",
        headers=headers(association),
        json={"action": "review", "version": 2, "comment": "核对冻结观测", "scores": {}},
    )
    assert reviewed.status_code == 200 and reviewed.json()["calculation"]["total"] == "90.00"


def test_future_indicator_blocks_whole_version_and_legacy_stays_unknown(configuration):
    owner, _association, sid, create, _eid = configuration
    future = (local_day() + timedelta(days=1)).isoformat()
    response = create(standard_body([indicator(effective_from=future)]))
    assert response.status_code == 201 and response.json()["activation_state"] == "scheduled"
    assert response.json()["effective_on"] == future
    assert apply(owner, sid, response.json()["id"], {"service": "80"}).status_code == 409
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(RatingApplication)
                .where(RatingApplication.standard_id == response.json()["id"])
            )
            == 0
        )
        assert capture(db, sid, {"indicators": []}) is None


def test_manual_raw_score_maps_then_weights_and_missing_is_not_zero(configuration):
    owner, _association, sid, create, _eid = configuration
    standard = create(standard_body()).json()
    response = apply(owner, sid, standard["id"], {})
    assert response.status_code == 201
    aid = response.json()["id"]
    path = f"/api/v1/ratings/applications/{aid}"
    detail = owner.get(path).json()
    assert detail["calculation"]["total"] is None and detail["calculation"]["missing"] == [
        "service"
    ]
    assert detail["calculation"]["details"][0]["missing_reason"] == "人工原值未填写"
    changed = owner.patch(
        path,
        headers=headers(owner),
        json={"version": 1, "scores": {"service": "80"}, "evidence_note": "按原值80分档"},
    )
    assert changed.status_code == 200, changed.text
    assert owner.get(path).json()["current_scores"]["service"] == "80"
    assert changed.json()["calculation"]["total"] == "100.00"
    frozen = owner.get(path).json()["rule_snapshot"]
    # A later version must not rewrite an existing application's rule/context evidence.
    other = standard_body()
    other["code"] = standard["code"]
    other["version"] = 2
    other["indicators"][0]["bands"][1]["score"] = "60"
    assert create(other).status_code == 201
    assert owner.get(path).json()["rule_snapshot"] == frozen


def test_standard_projection_failure_rolls_back_before_commit(configuration, monkeypatch):
    _owner, _association, _sid, create, _eid = configuration
    from app.routers import rating as route

    payload = standard_body()
    monkeypatch.setattr(
        route.RatingStandardResponse,
        "model_validate",
        Mock(side_effect=ValidationError.from_exception_data("RatingStandardResponse", [])),
    )
    with pytest.raises(ValidationError):
        create(payload)
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(RatingStandard)
                .where(RatingStandard.code == payload["code"])
            )
            == 0
        )
