import copy
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from alert_notification_samples import remove_for_events
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app import configured_alerts
from app.alert_configuration import Configuration
from app.configured_alert_contracts import ObservationInput
from app.db import SessionLocal
from app.main import app
from app.models import (
    AlertCursor,
    AlertEvent,
    AlertNotice,
    AlertRule,
    Audit,
    ConfiguredAlertObservation,
    Equipment,
    Station,
    User,
)


def configuration(**changes):
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


@pytest.fixture()
def setup(accounts, staff):
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.username == staff[0][0]))
        uid = user.id
        ids = []
        for index, config in enumerate(
            [
                configuration(),
                configuration(
                    kind="device_current",
                    thresholds={"general": "33.333367", "critical": "120"},
                    recover_value="30",
                ),
                configuration(
                    kind="station_offline",
                    direction="lte",
                    period="natural_week",
                    thresholds={"general": "30", "critical": "10"},
                    recover_value="40",
                ),
                configuration(
                    kind="station_fault",
                    period="natural_month",
                    thresholds={"general": "5", "critical": "20"},
                    recover_value="4",
                ),
                configuration(
                    kind="device_leakage",
                    thresholds={"general": "12", "critical": "25"},
                    recover_value="5",
                ),
                configuration(
                    kind="station_utilization",
                    direction="lte",
                    period="natural_month",
                    thresholds={"general": "20"},
                    recover_value="25",
                ),
            ]
        ):
            c = Configuration.model_validate(config)
            rule = AlertRule(
                code="exec-alert-" + secrets.token_hex(6),
                version=1,
                name=f"Configured {index}",
                metric=c.kind,
                unit="%" if c.kind.startswith("station_") else "demo",
                trigger_value=c.trigger,
                recover_value=c.recover_value,
                configuration=c.model_dump(mode="json"),
                is_demo=True,
                enabled=True,
            )
            db.add(rule)
            db.flush()
            ids.append(rule.id)
        equipment = []
        for sid in accounts[2]:
            row = Equipment(
                station_id=sid, external_id="demo-equipment", name="Scoped equipment", power_kw=60
            )
            db.add(row)
            db.flush()
            equipment.append(row.id)
    yield {
        "user": uid,
        "rules": ids,
        "station": accounts[2][0],
        "other_station": accounts[2][1],
        "equipment": equipment[0],
        "other_equipment": equipment[1],
    }
    with SessionLocal.begin() as db:
        observation_ids = select(ConfiguredAlertObservation.id).where(
            ConfiguredAlertObservation.rule_id.in_(ids)
        )
        db.execute(
            delete(Audit).where(
                Audit.action == "alerts.observation.accept", Audit.object_id.in_(observation_ids)
            )
        )
        db.execute(
            delete(ConfiguredAlertObservation).where(ConfiguredAlertObservation.rule_id.in_(ids))
        )
        events = select(AlertEvent.id).where(AlertEvent.rule_id.in_(ids))
        db.execute(delete(AlertCursor).where(AlertCursor.rule_id.in_(ids)))
        remove_for_events(db, events)
        db.execute(delete(AlertNotice).where(AlertNotice.event_id.in_(events)))
        db.execute(delete(AlertEvent).where(AlertEvent.rule_id.in_(ids)))
        db.execute(delete(AlertRule).where(AlertRule.id.in_(ids)))
        db.execute(delete(Equipment).where(Equipment.id.in_(equipment)))


def body(data, **changes):
    value = {
        "request_key": str(uuid4()),
        "station_id": data["station"],
        "equipment_id": data["equipment"],
        "rule_id": data["rules"][0],
        "observed_at": "2026-11-01T08:00:00+08:00",
        "value": "60",
        "coverage_complete": True,
    }
    value.update(changes)
    return ObservationInput.model_validate(value)


def submit(data, value):
    with SessionLocal.begin() as db:
        return configured_alerts.observe(db, db.get(User, data["user"]), value)


def test_levels_hysteresis_unknown_monotonic_receipts_and_frozen_first(setup):
    data = setup
    original = body(data)
    first = submit(data, original)
    assert first.outcome == "opened" and first.level == "general"
    assert submit(data, original) == first
    with pytest.raises(HTTPException) as conflict:
        submit(data, original.model_copy(update={"value": Decimal(61)}))
    assert conflict.value.status_code == 409
    with SessionLocal() as db:
        frozen = copy.deepcopy(db.get(AlertEvent, first.event_id).configuration_evidence["first"])
    for minute, value, outcome, level in [
        (1, "70", "merged", "severe"),
        (2, "80", "merged", "critical"),
        (3, "55", "merged", None),
        (4, None, "unknown_coverage", None),
        (5, "50", "recovered", None),
        (6, "90", "opened", "critical"),
    ]:
        result = submit(
            data,
            body(
                data,
                observed_at=f"2026-11-01T08:0{minute}:00+08:00",
                value=value,
                coverage_complete=value is not None,
            ),
        )
        assert result.outcome == outcome and result.level == level
        if minute == 4:
            with SessionLocal() as db:
                assert db.get(AlertEvent, first.event_id).status == "active"
        if minute == 6:
            assert result.event_id != first.event_id
    late = submit(data, body(data, value="100", observed_at="2026-11-01T08:01:00+08:00"))
    assert late.outcome == "ignored_stale"
    with SessionLocal() as db:
        event = db.get(AlertEvent, first.event_id)
        assert event.status == "recovered" and event.configuration_evidence["first"] == frozen
        assert event.configuration_evidence["highest_level"] == "critical"
        phases = list(
            db.scalars(select(AlertNotice.phase).where(AlertNotice.event_id == first.event_id))
        )
        assert sorted(phases) == ["level_critical", "level_severe", "opened", "recovered"]
        assert (
            db.scalar(
                select(func.count())
                .select_from(ConfiguredAlertObservation)
                .where(ConfiguredAlertObservation.rule_id == data["rules"][0])
            )
            == 8
        )


def test_current_uses_unrounded_ratio_and_numeric_overflow_is_atomic(setup):
    data = setup
    result = submit(
        data, body(data, rule_id=data["rules"][1], value="1.000001", rated_current_a="3")
    )
    assert result.outcome == "normal" and result.comparison_value == "33.333367"
    with pytest.raises(HTTPException) as error:
        submit(
            data,
            body(data, rule_id=data["rules"][1], value="999999999999", rated_current_a=".000001"),
        )
    assert error.value.status_code == 422
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(ConfiguredAlertObservation)
                .where(ConfiguredAlertObservation.rule_id == data["rules"][1])
            )
            == 1
        )


def test_closed_week_instances_do_not_merge_and_unknown_does_not_become_zero(setup):
    data = setup
    sample = body(
        data,
        rule_id=data["rules"][2],
        equipment_id=None,
        period_start="2026-10-19",
        period_end="2026-10-26",
        observed_at="2026-10-26T00:00:00+08:00",
        value="9",
    )
    first = submit(data, sample)
    assert first.level == "critical"
    second = submit(
        data,
        body(
            data,
            rule_id=data["rules"][2],
            equipment_id=None,
            period_start="2026-10-26",
            period_end="2026-11-02",
            observed_at="2026-11-02T00:00:00+08:00",
            value="20",
        ),
    )
    assert second.outcome == "opened" and second.event_id != first.event_id
    unknown = submit(
        data,
        body(
            data,
            rule_id=data["rules"][2],
            equipment_id=None,
            period_start="2026-10-26",
            period_end="2026-11-02",
            observed_at="2026-11-02T01:00:00+08:00",
            value=None,
            coverage_complete=False,
        ),
    )
    assert unknown.comparison_value is None and unknown.outcome == "unknown_coverage"
    stale = submit(
        data,
        body(
            data,
            rule_id=data["rules"][2],
            equipment_id=None,
            period_start="2026-10-26",
            period_end="2026-11-02",
            observed_at="2026-11-02T00:30:00+08:00",
            value="50",
        ),
    )
    assert stale.outcome == "ignored_stale"
    with SessionLocal() as db:
        assert db.get(AlertEvent, second.event_id).status == "active"
    with pytest.raises(HTTPException):
        submit(
            data,
            sample.model_copy(
                update={"request_key": uuid4(), "observed_at": datetime(2026, 10, 25, tzinfo=UTC)}
            ),
        )


def test_equipment_mapping_disabled_rule_and_period_shape_are_rejected(setup):
    data = setup
    for changed in [
        {"equipment_id": data["other_equipment"]},
        {"equipment_id": None},
        {"observed_at": "2027-01-01T00:00:00+08:00"},
        {"rule_id": data["rules"][2], "period_start": "2026-10-20", "period_end": "2026-10-27"},
    ]:
        with pytest.raises(HTTPException):
            submit(data, body(data, **changed))
    first = body(data)
    receipt = submit(data, first)
    with SessionLocal.begin() as db:
        db.get(AlertRule, data["rules"][0]).enabled = False
    assert submit(data, first) == receipt
    with pytest.raises(HTTPException):
        submit(data, body(data, observed_at="2026-11-01T09:00:00+08:00"))


def test_actual_concurrent_sessions_open_one_event_and_replay_one_receipt(setup):
    data = setup
    barrier = Barrier(2)
    sample = body(data)

    def run(_):
        barrier.wait(timeout=10)
        return submit(data, sample)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert results[0] == results[1]
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(AlertEvent)
                .where(AlertEvent.rule_id == data["rules"][0])
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(ConfiguredAlertObservation)
                .where(ConfiguredAlertObservation.rule_id == data["rules"][0])
            )
            == 1
        )


def test_api_receipt_scope_paging_and_unknown_nullable_value(setup, accounts, staff):
    data = setup
    clients = [TestClient(app, client=("cfg-api-" + secrets.token_hex(6), 1)) for _ in range(4)]
    try:
        headers = []
        for client, name in zip(
            clients, [staff[0][0], accounts[0][0], accounts[0][1], staff[0][1]], strict=True
        ):
            login = client.post(
                "/api/v1/auth/login", json={"username": name, "password": accounts[1]}
            )
            assert login.status_code == 200
            headers.append({"x-csrf-token": login.json()["csrf_token"]})
        path = "/api/v1/alerts/configured-observations"
        sample = body(data).model_dump(mode="json")
        result = clients[0].post(path, headers=headers[0], json=sample)
        assert result.status_code == 201, result.text
        replay = clients[0].post(path, headers=headers[0], json=sample)
        assert replay.json() == result.json()
        assert result.headers["cache-control"] == "no-store"
        detail = clients[0].get(f"/api/v1/alerts/{result.json()['event_id']}")
        assert detail.status_code == 200, detail.text
        assert (
            detail.json()["configuration_evidence"]["first"]["equipment"]["id"] == data["equipment"]
        )
        assert detail.headers["cache-control"] == "no-store"

        for index, expected in [(1, 1), (2, 0)]:
            page = clients[index].get(path, params={"station_id": data["station"], "page_size": 1})
            assert page.status_code == 200 and page.json()["total"] == expected
        assert clients[3].get(path).status_code == 403
        assert clients[1].post(path, headers=headers[1], json=sample).status_code == 403
        assert clients[0].get(path + "?page_size=101").status_code == 422
        unknown = clients[0].post(
            path,
            headers=headers[0],
            json=body(
                data, value=None, coverage_complete=False, observed_at="2026-11-01T09:00:00+08:00"
            ).model_dump(mode="json"),
        )
        assert unknown.status_code == 201 and unknown.json()["comparison_value"] is None
    finally:
        for client in clients:
            client.close()


def test_response_projection_failure_rolls_back_observation_cursor_event_and_audit(
    setup, accounts, staff, monkeypatch
):
    data = setup
    original = configured_alerts.observe

    def broken(*args, **kwargs):
        result = original(*args, **kwargs)
        return result.model_copy(update={"outcome": "invalid-output"})

    monkeypatch.setattr(configured_alerts, "observe", broken)
    with TestClient(
        app, client=("cfg-rollback-" + secrets.token_hex(6), 1), raise_server_exceptions=False
    ) as client:
        login = client.post(
            "/api/v1/auth/login", json={"username": staff[0][0], "password": accounts[1]}
        )
        response = client.post(
            "/api/v1/alerts/configured-observations",
            headers={"x-csrf-token": login.json()["csrf_token"]},
            json=body(data).model_dump(mode="json"),
        )
        assert response.status_code == 500
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(ConfiguredAlertObservation)
                .where(ConfiguredAlertObservation.rule_id.in_(data["rules"]))
            )
            == 0
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(AlertEvent)
                .where(AlertEvent.rule_id.in_(data["rules"]))
            )
            == 0
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(AlertCursor)
                .where(AlertCursor.rule_id.in_(data["rules"]))
            )
            == 0
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(Audit)
                .where(Audit.user_id == data["user"], Audit.action == "alerts.observation.accept")
            )
            == 0
        )


def test_samples_only_role_can_get_scoped_options_without_station_read(setup, staff, accounts):
    from app.models import RoleProfile

    data = setup
    station_query = "scoped-observation-options-" + secrets.token_hex(6)
    with SessionLocal.begin() as db:
        for station_id in (data["station"], data["other_station"]):
            db.get(Station, station_id).name = station_query
        profile = RoleProfile(
            name="observe-only-" + secrets.token_hex(6),
            base_role="regulator",
            permissions=["alerts.read", "alerts.samples"],
            version=1,
        )
        db.add(profile)
        db.flush()
        pid = profile.id
        user = db.get(User, data["user"])
        user.role_profile_id = pid
        user.allowed_districts = ["测试"]
    try:
        with TestClient(app, client=("cfg-options-" + secrets.token_hex(6), 1)) as client:
            login = client.post(
                "/api/v1/auth/login", json={"username": staff[0][0], "password": accounts[1]}
            )
            assert login.status_code == 200
            assert client.get("/api/v1/stations").status_code == 403
            result = client.get(
                f"/api/v1/alerts/configured-observations/stations?q={station_query}&page_size=1"
            )
            assert result.status_code == 200 and result.json()["total"] == 2
            equipment = client.get(
                f"/api/v1/alerts/configured-observations/equipment?station_id={data['station']}"
            )
            assert (
                equipment.status_code == 200
                and equipment.json()["items"][0]["id"] == data["equipment"]
            )
    finally:
        with SessionLocal.begin() as db:
            db.get(User, data["user"]).role_profile_id = None
            db.execute(delete(RoleProfile).where(RoleProfile.id == pid))


def test_receipt_fingerprint_uses_equivalent_numeric_and_timezone_values(setup):
    data = setup
    original = body(data, value="0")
    first = submit(data, original)
    equivalent = original.model_dump(mode="json")
    equivalent.update(value="-0.000000", observed_at="2026-11-01T00:00:00+00:00")
    assert submit(data, ObservationInput.model_validate(equivalent)) == first


def test_existing_event_configuration_cannot_be_reinterpreted_in_place(setup):
    data = setup
    first = submit(data, body(data))
    with SessionLocal.begin() as db:
        rule = db.get(AlertRule, data["rules"][0])
        changed = copy.deepcopy(rule.configuration)
        changed["thresholds"]["critical"] = "90"
        rule.configuration = changed
    with pytest.raises(HTTPException) as error:
        submit(data, body(data, value="85", observed_at="2026-11-01T08:01:00+08:00"))
    assert error.value.status_code == 409
    with SessionLocal() as db:
        assert db.get(AlertEvent, first.event_id).last_value == Decimal(60)
        assert (
            db.scalar(
                select(func.count())
                .select_from(ConfiguredAlertObservation)
                .where(ConfiguredAlertObservation.rule_id == data["rules"][0])
            )
            == 1
        )


def test_unrepresentable_timezone_boundary_is_rejected_before_fingerprinting():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ObservationInput.model_validate(
            {
                "request_key": str(uuid4()),
                "station_id": 1,
                "rule_id": 1,
                "equipment_id": 1,
                "value": "1",
                "coverage_complete": True,
                "observed_at": "0001-01-01T00:00:00+08:00",
            }
        )
