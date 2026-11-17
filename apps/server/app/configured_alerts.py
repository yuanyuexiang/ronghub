"""Independent configured demo observations: exact evaluation, receipts and frozen evidence."""

import hashlib
import json
from datetime import UTC, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.alert_configuration import LEVELS, Configuration, Preview
from app.alert_notifications import emit
from app.alerts import demo_only
from app.configured_alert_contracts import EventEvidence, ObservationEvidence, ObservationResult
from app.models import (
    AlertCursor,
    AlertEvent,
    AlertRule,
    ConfiguredAlertObservation,
    Equipment,
    Station,
    now,
)
from app.scopes import station_scope


def fingerprint(body):
    value = body.model_dump(mode="json", exclude={"request_key"})
    value["observed_at"] = body.observed_at.astimezone(UTC).isoformat()
    for field in ["value", "rated_current_a"]:
        number = getattr(body, field)
        value[field] = (
            ("0" if number == 0 else format(number.normalize(), "f"))
            if number is not None
            else None
        )
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def observe(db, user, body):
    if user.role != "regulator":
        raise HTTPException(403, "仅监管人员可提交演示观测")
    return _observe(db, user, body)


def observe_source(db, body, proof):
    """Internal adapter entry; never exposed as an HTTP actor or request field."""
    return _observe(db, None, body, proof=proof)


def _observe(db, user, body, proof=None):
    demo_only()
    station = db.scalar(
        (station_scope(select(Station), user) if user else select(Station))
        .where(Station.id == body.station_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not station:
        raise HTTPException(404, "场站不存在或不可访问")
    namespace = f"adapter-period:{proof.fact_id}:{body.rule_id}" if proof else f"user:{user.id}"
    digest = fingerprint(body)
    prior = db.scalar(
        select(ConfiguredAlertObservation).where(
            ConfiguredAlertObservation.namespace == namespace,
            ConfiguredAlertObservation.request_key == str(body.request_key),
        )
    )
    if prior:
        if prior.fingerprint != digest:
            raise HTTPException(409, "同一请求编号对应的观测已存在，不能改变内容重试")
        return ObservationResult.model_validate(prior.result)
    if body.observed_at > now() + timedelta(minutes=5):
        raise HTTPException(422, "观测时间不能超前超过5分钟")
    rule = db.scalar(
        select(AlertRule)
        .where(AlertRule.id == body.rule_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if (
        not rule
        or rule.configuration is None
        or not rule.is_demo
        or not rule.enabled
        or rule.archived
    ):
        raise HTTPException(409, "分级规则不存在、已停用或已归档")
    try:
        configuration = Configuration.model_validate(rule.configuration)
        validated = Preview.model_validate(
            {
                "configuration": configuration,
                **body.model_dump(
                    exclude={"request_key", "station_id", "rule_id", "equipment_id", "observed_at"}
                ),
            }
        )
    except ValidationError as error:
        raise HTTPException(
            422, "独立观测口径不匹配：请核对完整周期、覆盖、额定值和单位"
        ) from error
    periodic = configuration.period != "instant"
    equipment = None
    if periodic:
        if body.equipment_id is not None:
            raise HTTPException(422, "场站周期指标不能绑定单台设备")
        end = datetime.combine(body.period_end, time.min, ZoneInfo("Asia/Shanghai"))
        if body.observed_at < end:
            raise HTTPException(422, "周期观测时间不得早于完整周期结束")
        stream = (
            f"adapter-period:{proof.fact_id}"
            if proof
            else f"configured-period:{body.period_start.isoformat()}"
        )
        target = f"场站周期 {body.period_start.isoformat()}"
    else:
        if body.equipment_id is None:
            raise HTTPException(422, "设备观测必须精确选择所属场站设备")
        equipment = db.scalar(
            select(Equipment)
            .where(Equipment.id == body.equipment_id, Equipment.station_id == station.id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        if not equipment:
            raise HTTPException(404, "设备不存在或不属于该场站")
        stream = f"configured-equipment:{equipment.id}"
        target = stream
    with localcontext() as context:
        context.prec = 50
        exact = (
            (
                validated.value * 100 / validated.rated_current_a
                if configuration.kind == "device_current"
                else validated.value
            )
            if validated.coverage_complete
            else None
        )
        if proof:
            if (
                proof.source != station.source
                or proof.source_record.metric != configuration.kind
                or proof.source_record.period != configuration.period
                or proof.source_record.period_start != body.period_start
                or proof.source_record.period_end != body.period_end
            ):
                raise HTTPException(409, "独立来源与场站规则周期不一致")
            exact = (
                Decimal(proof.numerator_seconds) * 100 / Decimal(proof.denominator_seconds)
                if proof.quality_reason is None
                else None
            )
        if exact is not None and abs(exact) > Decimal("999999999999.999999"):
            raise HTTPException(422, "标准化观测超出可存储范围，整次观测未提交")
        shown = (
            exact.quantize(Decimal(".000001"), rounding=ROUND_HALF_UP)
            if exact is not None
            else None
        )
        evidence = ObservationEvidence(
            evidence_version=1,
            source_basis="adapter_period_demo_v1" if proof else "manual_demo_v1",
            source_period=proof,
            operator_id=station.operator_id,
            station_id=station.id,
            station_source=station.source,
            station_external_id=station.external_id,
            equipment={
                "id": equipment.id,
                "external_id": equipment.external_id,
                "name": equipment.name,
            }
            if equipment
            else None,
            rule_id=rule.id,
            rule_code=rule.code,
            rule_version=rule.version,
            configuration=configuration,
            observed_at=body.observed_at.isoformat(),
            value=str(body.value) if body.value is not None else None,
            rated_current_a=str(body.rated_current_a) if body.rated_current_a is not None else None,
            period_start=body.period_start.isoformat() if body.period_start else None,
            period_end=body.period_end.isoformat() if body.period_end else None,
            coverage_complete=body.coverage_complete,
            comparison_value=str(shown) if shown is not None else None,
            is_demo=True,
        )
        observation = ConfiguredAlertObservation(
            namespace=namespace,
            request_key=str(body.request_key),
            fingerprint=digest,
            station_id=station.id,
            operator_id=station.operator_id,
            rule_id=rule.id,
            created_by=user.id if user else None,
            evidence=evidence.model_dump(mode="json"),
            result={},
        )
        db.add(observation)
        db.flush()
        cursor = db.scalar(
            select(AlertCursor)
            .where(
                AlertCursor.rule_id == rule.id,
                AlertCursor.station_id == station.id,
                AlertCursor.target == stream,
            )
            .execution_options(populate_existing=True)
        )
        event = (
            db.get(AlertEvent, cursor.active_event_id)
            if cursor and cursor.active_event_id
            else None
        )
        phase = None
        level = None
        if cursor and body.observed_at <= cursor.observed_at:
            outcome = "ignored_stale"
        else:
            if cursor is None:
                cursor = AlertCursor(
                    rule_id=rule.id,
                    station_id=station.id,
                    target=stream,
                    observed_at=body.observed_at,
                )
                db.add(cursor)
            cursor.observed_at = body.observed_at
            if exact is None:
                outcome = "unknown_coverage"
            else:

                def compares(threshold, direction):
                    left = Decimal(proof.numerator_seconds) * 100 if proof else exact
                    right = threshold * Decimal(proof.denominator_seconds) if proof else threshold
                    return left >= right if direction == "gte" else left <= right

                level = None
                for candidate in LEVELS:
                    threshold = configuration.thresholds.get(candidate)
                    if threshold is not None and compares(threshold, configuration.direction):
                        level = candidate
                if event:
                    try:
                        state = EventEvidence.model_validate(event.configuration_evidence)
                    except ValidationError as error:
                        raise HTTPException(409, "既有分级事件证据损坏，未覆盖历史") from error
                    if state.first.configuration != configuration:
                        raise HTTPException(409, "原事件冻结配置与规则版本不一致，未覆盖历史")
                    recovered = compares(
                        configuration.recover_value,
                        "lte" if configuration.direction == "gte" else "gte",
                    )
                    highest = state.highest_level
                    if level is not None and LEVELS.index(level) > LEVELS.index(highest):
                        highest = level
                        phase = f"level_{level}"
                    event.last_value = shown
                    event.last_observed_at = body.observed_at
                    event.version += 1
                    if recovered:
                        event.status = "recovered"
                        event.recovered_at = body.observed_at
                        cursor.active_event_id = None
                        outcome = phase = "recovered"
                        level = None
                    else:
                        event.occurrences += 1
                        outcome = "merged"
                    original_first = event.configuration_evidence["first"]
                    event.configuration_evidence = EventEvidence(
                        evidence_version=1,
                        first_observation_id=state.first_observation_id,
                        latest_observation_id=observation.id,
                        first=state.first,
                        latest=evidence,
                        current_level=level,
                        highest_level=highest,
                    ).model_dump(mode="json")
                    event.configuration_evidence["first"] = original_first
                elif level is not None:
                    state = EventEvidence(
                        evidence_version=1,
                        first_observation_id=observation.id,
                        latest_observation_id=observation.id,
                        first=evidence,
                        latest=evidence,
                        current_level=level,
                        highest_level=level,
                    )
                    event = AlertEvent(
                        rule_id=rule.id,
                        station_id=station.id,
                        operator_id=station.operator_id,
                        target=target,
                        opened_at=body.observed_at,
                        last_observed_at=body.observed_at,
                        last_value=shown,
                        is_demo=True,
                        configuration_evidence=state.model_dump(mode="json"),
                        rule_snapshot={
                            "code": rule.code,
                            "version": rule.version,
                            "metric": rule.metric,
                            "unit": rule.unit,
                            "trigger": str(configuration.trigger),
                            "recover": str(configuration.recover_value),
                            "notifications_enabled": rule.notifications_enabled,
                            "configuration": configuration.model_dump(mode="json"),
                        },
                    )
                    db.add(event)
                    db.flush()
                    cursor.active_event_id = event.id
                    outcome = phase = "opened"
                else:
                    outcome = "normal"
        if phase:
            labels = {
                "opened": "触发",
                "recovered": "恢复",
                "level_severe": "升级为严重",
                "level_critical": "升级为重大" if periodic else "升级为紧急",
            }
            emit(db, event, rule, phase, f"演示分级告警：{rule.name} · {labels[phase]} · {target}")
        observation.event_id = event.id if event else None
        result = ObservationResult(
            observation_id=observation.id,
            request_key=observation.request_key,
            outcome=outcome,
            event_id=observation.event_id,
            level=level,
            comparison_value=evidence.comparison_value,
            is_demo=True,
        )
        observation.result = result.model_dump(mode="json")
    return result
