"""Transactional adapter-only ingestion of historical period facts."""

from datetime import datetime, time
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select

from app.alert_period_contracts import BEIJING, SourcePeriod
from app.alerts import demo_only
from app.models import AlertPeriodFact, Equipment, Station, now


def canonical(record: SourcePeriod):
    payload = record.model_dump(mode="json")
    for member in payload["members"]:
        for key in ("expected_seconds", "observed_seconds", "unknown_seconds", "affected_seconds"):
            if member[key] is not None:
                number = Decimal(member[key])
                member[key] = "0" if number == 0 else format(number.normalize(), "f")
    return payload


def ingest(db, *, station_id: int, operator_id: int, source: str, records: list[dict]):
    """Caller owns commit; a savepoint rolls back the whole incoming batch on failure."""
    demo_only()
    if len(records) > 100:
        raise HTTPException(422, "周期来源单批不能超过100条")
    parsed = [SourcePeriod.model_validate(value) for value in records]
    identities = [(value.metric, value.period, value.period_start) for value in parsed]
    if len({value.external_id for value in parsed}) != len(parsed) or len(set(identities)) != len(
        parsed
    ):
        raise HTTPException(422, "同批周期来源标识或自然周期重复")
    with db.begin_nested():
        station = db.scalar(
            select(Station)
            .where(Station.id == station_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if not station or station.operator_id != operator_id or station.source != source:
            raise HTTPException(409, "周期来源与场站归属不匹配")
        known = set(
            db.scalars(select(Equipment.external_id).where(Equipment.station_id == station.id))
        )
        counts = {"created": 0, "updated": 0, "unchanged": 0, "stale": 0}
        for record in parsed:
            if not set(record.equipment_external_ids) <= known:
                raise HTTPException(409, "来源声明的历史设备尚未映射到该场站")
            fact = db.scalar(
                select(AlertPeriodFact)
                .where(
                    AlertPeriodFact.source == source,
                    AlertPeriodFact.operator_id == operator_id,
                    AlertPeriodFact.external_id == record.external_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            payload = canonical(record)
            if fact:
                if (fact.station_id, fact.metric, fact.period, fact.period_start) != (
                    station.id,
                    record.metric,
                    record.period,
                    record.period_start,
                ):
                    raise HTTPException(409, "已有来源标识不能改绑场站或周期")
                if record.source_updated_at < fact.source_updated_at:
                    counts["stale"] += 1
                    continue
                if record.source_updated_at == fact.source_updated_at:
                    if payload != fact.payload:
                        raise HTTPException(409, "同一来源修订时间出现不同内容")
                    counts["unchanged"] += 1
                    continue
                fact.version += 1
                counts["updated"] += 1
            else:
                duplicate = db.scalar(
                    select(AlertPeriodFact.id).where(
                        AlertPeriodFact.source == source,
                        AlertPeriodFact.station_id == station.id,
                        AlertPeriodFact.metric == record.metric,
                        AlertPeriodFact.period == record.period,
                        AlertPeriodFact.period_start == record.period_start,
                    )
                )
                if duplicate:
                    raise HTTPException(409, "同一自然周期不能更换来源标识")
                fact = AlertPeriodFact(
                    station_id=station.id,
                    operator_id=operator_id,
                    source=source,
                    external_id=record.external_id,
                    metric=record.metric,
                    period=record.period,
                    period_start=record.period_start,
                    version=1,
                    is_demo=True,
                    period_closed_at=datetime.combine(record.period_end, time.min, BEIJING),
                )
                db.add(fact)
                counts["created"] += 1
            fact.payload = payload
            fact.source_updated_at = record.source_updated_at
            fact.synced_at = now()
            db.flush()
    return counts
