import secrets
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, func, select

from app.alert_configuration import Configuration, Preview
from app.db import SessionLocal
from app.main import app
from app.models import AlertRule, Audit, User


def config(**changes):
    result = {
        "configuration_version": 1,
        "kind": "device_temperature",
        "direction": "gte",
        "period": "instant",
        "thresholds": {"general": "60", "severe": "70", "critical": "80"},
        "recover_value": "50",
    }
    result.update(changes)
    return result


@pytest.mark.parametrize(
    "change",
    [
        {"configuration_version": True},
        {"configuration_version": 2},
        {"thresholds": {}},
        {"thresholds": {"general": "70", "severe": "60"}},
        {"thresholds": {"general": "70", "severe": "70"}},
        {"thresholds": {"unknown": "10"}},
        {"thresholds": {"general": "NaN"}},
        {"recover_value": "60"},
        {"period": "natural_month"},
        {"kind": "station_fault"},
        {"extra": "ignored?"},
    ],
)
def test_configuration_rejects_ambiguous_or_inconsistent_rules(change):
    with pytest.raises(ValidationError):
        Configuration.model_validate(config(**change))


def test_direction_levels_exact_boundaries_and_period_coverage():
    high = Configuration.model_validate(config())
    assert high.level(Decimal(80)) == "critical"
    assert high.level(Decimal("79.999999")) == "severe"
    low = Configuration.model_validate(
        config(direction="lte", thresholds={"general": "30", "critical": "10"}, recover_value="40")
    )
    assert low.level(Decimal(10)) == "critical" and low.level(Decimal(31)) is None
    month = config(
        kind="station_fault",
        period="natural_month",
        thresholds={"general": "5", "critical": "20"},
        recover_value="4",
    )
    data = {
        "configuration": month,
        "value": "10",
        "coverage_complete": True,
        "period_start": "2026-10-01",
        "period_end": "2026-11-01",
    }
    assert Preview.model_validate(data).period_end == date(2026, 11, 1)
    for changed in [
        {"period_end": "2026-10-31"},
        {"period_start": "2026-10-02"},
        {"period_end": "9999-12-31"},
    ]:
        with pytest.raises(ValidationError):
            Preview.model_validate({**data, **changed})
    with pytest.raises(ValidationError):
        Preview.model_validate(
            {
                "configuration": config(kind="device_current"),
                "value": "10",
                "coverage_complete": True,
            }
        )


@pytest.fixture()
def api(staff):
    username = staff[0][0]
    client = TestClient(app, client=("config-" + secrets.token_hex(6), 1))
    login = client.post("/api/v1/auth/login", json={"username": username, "password": staff[1]})
    assert login.status_code == 200
    with SessionLocal() as db:
        uid = db.scalar(select(User.id).where(User.username == username))
    try:
        yield client, {"x-csrf-token": login.json()["csrf_token"]}, username
    finally:
        client.close()
        with SessionLocal.begin() as db:
            ids = list(db.scalars(select(AlertRule.id).where(AlertRule.created_by == uid)))
            db.execute(
                delete(Audit).where(
                    Audit.user_id == uid, Audit.action.like("alerts.configuration.%")
                )
            )
            db.execute(delete(AlertRule).where(AlertRule.id.in_(ids)))


def create_body():
    code = "test-config-" + secrets.token_hex(6)
    return {
        "code": code,
        "version": 1,
        "name": "分级演示",
        "configuration": config(),
        "notifications_enabled": True,
    }


def test_configuration_creation_lifecycle_version_conflicts_and_legacy_guard(api, accounts):
    client, headers, username = api
    path = "/api/v1/alerts/configured-rules"
    body = create_body()
    response = client.post(path, json=body, headers=headers)
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["configuration"]["thresholds"]["critical"] == "80"
    assert item["created_by_name"] == username and item["execution_supported"] is True
    assert response.headers["cache-control"] == "no-store"
    rid = item["id"]
    assert client.post(path, json=body, headers=headers).status_code == 409
    body["version"] = 2
    assert client.post(path, json=body, headers=headers).status_code == 201
    listing = client.get(path, params={"q": body["code"], "page_size": 1})
    assert listing.status_code == 200 and listing.json()["total"] == 2
    assert listing.headers["cache-control"] == "no-store"
    assert not any(row["id"] == rid for row in client.get("/api/v1/alerts/rules").json()["items"])
    blocked = client.post(
        "/api/v1/alerts/samples",
        headers=headers,
        json={
            "station_id": accounts[2][0],
            "rule_id": rid,
            "target": "test",
            "value": "90",
            "observed_at": "2026-11-01T00:00:00+08:00",
        },
    )
    assert blocked.status_code == 409
    updated = client.patch(
        f"{path}/{rid}/availability", headers=headers, json={"revision": 1, "enabled": False}
    )
    assert updated.status_code == 200 and updated.json()["revision"] == 2
    assert (
        client.patch(
            f"{path}/{rid}/availability", headers=headers, json={"revision": 1, "enabled": True}
        ).status_code
        == 409
    )
    archived = client.delete(f"{path}/{rid}?revision=2", headers=headers)
    assert (
        archived.status_code == 200
        and archived.json()["archived"] is True
        and not archived.json()["enabled"]
    )
    assert client.get(path, params={"q": body["code"]}).json()["total"] == 1
    assert (
        client.get(path, params={"q": body["code"], "include_archived": True}).json()["total"] == 2
    )
    assert (
        client.patch(
            f"{path}/{rid}/availability", headers=headers, json={"revision": 3, "enabled": True}
        ).status_code
        == 409
    )


def test_preview_uses_exact_rated_current_and_does_not_persist(api):
    client, headers, _ = api
    path = "/api/v1/alerts/configured-rules/preview"
    c = config(kind="device_current", thresholds={"general": "100.000001"}, recover_value="90")
    body = {
        "configuration": c,
        "value": "1.000000",
        "rated_current_a": ".999999",
        "coverage_complete": True,
    }
    with SessionLocal() as db:
        count = db.scalar(select(func.count()).select_from(AlertRule))
        audits = db.scalar(select(func.count()).select_from(Audit))
    result = client.post(path, headers=headers, json=body)
    assert result.status_code == 200, result.text
    assert result.json()["level"] == "general" and result.json()["comparison_value"] == "100.000100"
    result = client.post(path, headers=headers, json={**body, "coverage_complete": False})
    assert result.json()["verdict"] == "unknown" and result.json()["comparison_value"] is None
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AlertRule)) == count
        assert db.scalar(select(func.count()).select_from(Audit)) == audits


def test_operator_and_regional_manager_cannot_mutate_global_rules(api, accounts):
    _, _, username = api
    with TestClient(app, client=("op-config-" + secrets.token_hex(6), 1)) as client:
        login = client.post(
            "/api/v1/auth/login", json={"username": accounts[0][0], "password": accounts[1]}
        )
        assert login.status_code == 200
        assert (
            client.post(
                "/api/v1/alerts/configured-rules",
                headers={"x-csrf-token": login.json()["csrf_token"]},
                json=create_body(),
            ).status_code
            == 403
        )
    client, headers, _ = api
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.username == username))
        user.allowed_districts = []
    assert (
        client.post(
            "/api/v1/alerts/configured-rules", headers=headers, json=create_body()
        ).status_code
        == 403
    )


def test_projection_failure_rolls_back_configuration_and_audit(api, monkeypatch):
    from app.routers import alert_configuration

    client, headers, _ = api
    body = create_body()
    original = alert_configuration.output

    def broken(*args, **kwargs):
        value = original(*args, **kwargs).model_dump()
        value["configuration"] = None
        return alert_configuration.RuleResponse.model_validate(value)

    monkeypatch.setattr(alert_configuration, "output", broken)
    with pytest.raises(ValidationError):
        client.post("/api/v1/alerts/configured-rules", headers=headers, json=body)
    with SessionLocal() as db:
        assert db.scalar(select(AlertRule.id).where(AlertRule.code == body["code"])) is None


def test_preview_does_not_compare_rounded_display_value(api):
    client, headers, _ = api
    body = {
        "configuration": config(
            kind="device_current", thresholds={"general": "33.333367"}, recover_value="30"
        ),
        "value": "1.000001",
        "rated_current_a": "3",
        "coverage_complete": True,
    }
    result = client.post("/api/v1/alerts/configured-rules/preview", headers=headers, json=body)
    assert result.status_code == 200
    assert result.json()["comparison_value"] == "33.333367"
    assert result.json()["verdict"] == "normal"


def test_concurrent_lifecycle_writes_use_actual_postgres_revision_lock(api, staff):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    client, headers, username = api
    path = "/api/v1/alerts/configured-rules"
    created = client.post(path, headers=headers, json=create_body()).json()
    barrier = Barrier(2)

    def change(_):
        with TestClient(app, client=("cfg-thread-" + secrets.token_hex(6), 1)) as connection:
            login = connection.post(
                "/api/v1/auth/login", json={"username": username, "password": staff[1]}
            )
            assert login.status_code == 200
            barrier.wait(timeout=10)
            return connection.patch(
                f"{path}/{created['id']}/availability",
                headers={"x-csrf-token": login.json()["csrf_token"]},
                json={"revision": 1, "enabled": False},
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(change, range(2))) == [200, 409]
    with SessionLocal() as db:
        assert db.get(AlertRule, created["id"]).revision == 2
        assert (
            db.scalar(
                select(func.count())
                .select_from(Audit)
                .where(
                    Audit.object_id == created["id"],
                    Audit.action == "alerts.configuration.availability",
                )
            )
            == 1
        )


def test_rule_group_filters_have_server_counts_and_intersect_with_kind(api):
    client, headers, _ = api
    prefix = "group-" + secrets.token_hex(6)
    for kind in ["device_temperature", "station_utilization"]:
        body = create_body()
        body["name"] = prefix
        body["configuration"] = config(
            **({"kind": kind, "period": "natural_month"} if kind.startswith("station_") else {})
        )
        assert (
            client.post("/api/v1/alerts/configured-rules", headers=headers, json=body).status_code
            == 201
        )
    for group in ["station", "device"]:
        response = client.get(
            "/api/v1/alerts/configured-rules", params={"q": prefix, "kind_group": group}
        )
        assert response.status_code == 200
        assert response.json()["total"] == 1
        assert response.json()["items"][0]["configuration"]["kind"].startswith(group + "_")
    assert (
        client.get(
            "/api/v1/alerts/configured-rules",
            params={"q": prefix, "kind_group": "station", "kind": "device_temperature"},
        ).json()["total"]
        == 0
    )
    assert client.get("/api/v1/alerts/configured-rules?kind_group=invalid").status_code == 422
