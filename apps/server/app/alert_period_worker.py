"""Durable demo period evaluation: no user impersonation or historical rule inference."""

from decimal import ROUND_HALF_UP, Decimal, localcontext
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import exists, select, text
from sqlalchemy.orm import aliased

from app.alert_configuration import Configuration
from app.alert_period_contracts import SourcePeriod, SourcePeriodProof, aggregate
from app.config import settings
from app.configured_alert_contracts import ObservationInput
from app.configured_alerts import observe_source
from app.db import SessionLocal
from app.models import AlertPeriodEvaluation, AlertPeriodFact, AlertRule, Station

RULE_LOCK = 78230014


def rule_lock(db, *, shared=False):
    name = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
    db.execute(text(f"SELECT {name}(:key)"), {"key": RULE_LOCK})


def pending():
    newer = aliased(AlertRule)
    return (
        select(AlertPeriodFact.id, AlertPeriodFact.station_id, AlertRule.id)
        .join(AlertRule, AlertRule.metric == AlertPeriodFact.metric)
        .where(
            AlertRule.configuration.is_not(None),
            AlertRule.enabled.is_(True),
            AlertRule.archived.is_(False),
            AlertRule.is_demo.is_(True),
            AlertPeriodFact.is_demo.is_(True),
            AlertRule.configuration["period"].as_string() == AlertPeriodFact.period,
            AlertRule.monitor_from.is_not(None),
            AlertPeriodFact.period_closed_at >= AlertRule.monitor_from,
            ~exists(
                select(newer.id).where(
                    newer.code == AlertRule.code, newer.version > AlertRule.version
                )
            ),
            ~exists(
                select(AlertPeriodEvaluation.id).where(
                    AlertPeriodEvaluation.fact_id == AlertPeriodFact.id,
                    AlertPeriodEvaluation.fact_version == AlertPeriodFact.version,
                    AlertPeriodEvaluation.rule_id == AlertRule.id,
                )
            ),
        )
        .order_by(AlertPeriodFact.period_closed_at, AlertPeriodFact.id, AlertRule.id)
    )


def process_one():
    if not settings.demo_mode:
        return False
    with SessionLocal.begin() as db:
        rule_lock(db, shared=True)
        candidate = db.execute(pending().limit(1)).first()
        if candidate is None:
            return False
        fact_id, station_id, rule_id = candidate
        # Same station lock/order as ingestion and manual observations. Recheck after waiting.
        db.scalar(select(Station).where(Station.id == station_id).with_for_update())
        fact = db.scalar(
            select(AlertPeriodFact)
            .where(AlertPeriodFact.id == fact_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        eligible = db.execute(
            pending().where(AlertPeriodFact.id == fact_id, AlertRule.id == rule_id)
        ).first()
        if eligible is None:
            return True
        record = SourcePeriod.model_validate(fact.payload)
        rule = db.get(AlertRule, rule_id)
        Configuration.model_validate(rule.configuration)
        numerator, denominator, reason = aggregate(record)
        proof = SourcePeriodProof(
            fact_id=fact.id,
            fact_version=fact.version,
            source=fact.source,
            source_record=record,
            numerator_seconds=str(numerator) if reason is None else None,
            denominator_seconds=str(denominator) if reason is None else None,
            quality_reason=reason,
        )
        with localcontext() as context:
            context.prec = 50
            shown = (
                (numerator * 100 / denominator).quantize(Decimal(".000001"), rounding=ROUND_HALF_UP)
                if reason is None
                else None
            )
        body = ObservationInput(
            request_key=uuid5(NAMESPACE_URL, f"ronghub:period:{fact.id}:{fact.version}:{rule.id}"),
            station_id=fact.station_id,
            rule_id=rule.id,
            observed_at=fact.source_updated_at,
            value=shown,
            coverage_complete=reason is None,
            period_start=record.period_start,
            period_end=record.period_end,
        )
        result = observe_source(db, body, proof)
        db.add(
            AlertPeriodEvaluation(
                fact_id=fact.id,
                fact_version=fact.version,
                rule_id=rule.id,
                observation_id=result.observation_id,
            )
        )
        db.flush()
    return True
