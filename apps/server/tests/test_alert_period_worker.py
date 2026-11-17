from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select
from test_alert_period_facts import record
from test_alert_period_facts import source as source  # noqa: PLC0414
from test_configured_alerts import setup as setup  # noqa: PLC0414

from app import alert_period_worker
from app.alert_period_facts import ingest
from app.alert_period_worker import process_one
from app.db import SessionLocal
from app.models import (
    AlertEvent,
    AlertPeriodEvaluation,
    AlertPeriodFact,
    AlertRule,
    ConfiguredAlertObservation,
)


@pytest.fixture()
def automatic(source, setup, monkeypatch):
    rule_id = setup["rules"][3]
    with SessionLocal.begin() as db:
        rule = db.get(AlertRule, rule_id)
        rule.configuration = {
            **rule.configuration,
            "period": "natural_week",
            "thresholds": {"general": "33.333333"},
            "recover_value": "20",
        }
        rule.monitor_from = datetime(2026, 10, 1, tzinfo=UTC)
        payload = record()
        payload["members"][0].update(
            expected_seconds="3", observed_seconds="3", affected_seconds="1"
        )
        ingest(db, **source, records=[payload])
    # The development database also contains browser-created source facts.
    # Keep the real eligibility/locking query, but consume only this fixture's
    # rule and station instead of asserting that the entire demo queue is empty.
    original_pending = alert_period_worker.pending
    monkeypatch.setattr(
        alert_period_worker,
        "pending",
        lambda: original_pending().where(
            AlertPeriodFact.station_id == source["station_id"], AlertRule.id == rule_id
        ),
    )
    yield source, setup, rule_id
    with SessionLocal.begin() as db:
        db.execute(delete(AlertPeriodEvaluation).where(AlertPeriodEvaluation.rule_id == rule_id))


def test_worker_precise_comparison_concurrent_receipt_and_unknown(automatic):
    source, _setup, rule_id = automatic
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: process_one(), range(2)))
    with SessionLocal.begin() as db:
        evaluations = list(
            db.scalars(
                select(AlertPeriodEvaluation).where(AlertPeriodEvaluation.rule_id == rule_id)
            )
        )
        assert len(evaluations) == 1
        observation = db.get(ConfiguredAlertObservation, evaluations[0].observation_id)
        assert observation.created_by is None
        assert observation.result["outcome"] == "opened"
        assert observation.result["comparison_value"] == "33.333333"
        event = db.get(AlertEvent, observation.event_id)
        first = event.configuration_evidence["first"]
        assert first["source_period"]["fact_version"] == 1
        assert first["source_basis"] == "adapter_period_demo_v1"
        eid = event.id
        payload = record(source_updated_at="2026-10-13T00:00:00+08:00")
        payload["members"][0].update(
            expected_seconds="3", observed_seconds="3", unknown_seconds="1", affected_seconds="0"
        )
        ingest(db, **source, records=[payload])
    assert process_one()
    assert not process_one()
    with SessionLocal.begin() as db:
        event = db.get(AlertEvent, eid)
        assert event.status != "recovered"
        assert event.configuration_evidence["first"] == first
        assert event.configuration_evidence["latest"]["source_period"]["fact_version"] == 1
        last = db.scalar(
            select(ConfiguredAlertObservation)
            .where(ConfiguredAlertObservation.rule_id == rule_id)
            .order_by(ConfiguredAlertObservation.id.desc())
        )
        assert last.result["outcome"] == "unknown_coverage"
        assert last.evidence["source_period"]["quality_reason"] == "unknown_states"
        payload["source_updated_at"] = "2026-10-14T00:00:00+08:00"
        payload["members"][0]["unknown_seconds"] = "0"
        ingest(db, **source, records=[payload])
    assert process_one()
    with SessionLocal() as db:
        event = db.get(AlertEvent, eid)
        assert event.status == "recovered"
        assert event.configuration_evidence["first"] == first
        assert event.configuration_evidence["latest"]["source_period"]["fact_version"] == 3


def test_monitor_start_and_highest_version_disabled_never_falls_back(automatic):
    _, _, rid = automatic
    with SessionLocal.begin() as db:
        rule = db.get(AlertRule, rid)
        rule.monitor_from = None
    assert not process_one()
    with SessionLocal.begin() as db:
        rule = db.get(AlertRule, rid)
        rule.monitor_from = datetime(2026, 10, 13, tzinfo=UTC)
    assert not process_one()
    with SessionLocal.begin() as db:
        rule = db.get(AlertRule, rid)
        rule.monitor_from = datetime(2026, 10, 1, tzinfo=UTC)
        newer = AlertRule(
            code=rule.code,
            version=2,
            name="disabled head",
            metric=rule.metric,
            unit=rule.unit,
            trigger_value=rule.trigger_value,
            recover_value=rule.recover_value,
            configuration=rule.configuration,
            enabled=False,
            is_demo=True,
            monitor_from=rule.monitor_from,
        )
        db.add(newer)
        db.flush()
        nid = newer.id
    try:
        assert not process_one()
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(AlertRule).where(AlertRule.id == nid))
    assert process_one()


def test_actual_worker_once_consumes_without_a_sync_job(automatic, monkeypatch):
    import sys

    from app import sync

    monkeypatch.setattr(sync, "claim", lambda: None)
    monkeypatch.setattr(sys, "argv", ["worker", "--once"])
    sync.main()
    with SessionLocal() as db:
        evaluation = db.scalar(
            select(AlertPeriodEvaluation).where(AlertPeriodEvaluation.rule_id == automatic[2])
        )
        assert evaluation is not None
        observation = db.get(ConfiguredAlertObservation, evaluation.observation_id)
        assert observation.created_by is None and observation.result["outcome"] == "opened"


def test_source_ratio_must_not_round_into_low_threshold_alert(automatic):
    # 1/3 displays 33.333333%, but is strictly ABOVE that low-value threshold.
    with SessionLocal.begin() as db:
        rule = db.get(AlertRule, automatic[2])
        rule.configuration = {**rule.configuration, "direction": "lte", "recover_value": "40"}
    assert process_one()
    with SessionLocal() as db:
        evaluation = db.scalar(
            select(AlertPeriodEvaluation).where(AlertPeriodEvaluation.rule_id == automatic[2])
        )
        observation = db.get(ConfiguredAlertObservation, evaluation.observation_id)
        assert observation.result["comparison_value"] == "33.333333"
        assert observation.result["outcome"] == "normal" and observation.event_id is None
