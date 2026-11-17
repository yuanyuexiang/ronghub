"""Demo metric evaluator with monotonic samples, hysteresis and persisted deduplication."""

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select

from app.alert_notifications import emit
from app.config import settings
from app.models import AlertCursor, AlertEvent, AlertRule, Station, now
from app.scopes import records as scoped_records
from app.scopes import station_scope


def demo_only():
    if not settings.demo_mode:
        raise HTTPException(409, "告警来源与规则待确认，示例写入仅限演示环境")


def observe(db, user, station_id, rule_id, target, value, observed_at):
    demo_only()
    if user.role != "regulator":
        raise HTTPException(403, "仅监管人员可注入演示样本")
    if observed_at.tzinfo is None or observed_at > now() + timedelta(minutes=5):
        raise HTTPException(422, "样本时间需带时区且不能超前超过5分钟")
    station = db.scalar(
        station_scope(select(Station), user).where(Station.id == station_id).with_for_update()
    )
    if not station:
        raise HTTPException(404, "场站不存在")
    rule = db.get(AlertRule, rule_id)
    if not rule or not rule.enabled or not rule.is_demo or rule.archived:
        raise HTTPException(409, "示例规则不存在或已停用")
    if rule.configuration is not None:
        raise HTTPException(409, "多级规则须使用独立观测口径，不能套用旧单阈值算法")
    cursor = db.scalar(
        select(AlertCursor).where(
            AlertCursor.rule_id == rule_id,
            AlertCursor.station_id == station_id,
            AlertCursor.target == target,
        )
    )
    if cursor and observed_at <= cursor.observed_at:
        return {"outcome": "ignored_stale", "event_id": cursor.active_event_id}
    if not cursor:
        cursor = AlertCursor(
            rule_id=rule_id, station_id=station_id, target=target, observed_at=observed_at
        )
        db.add(cursor)
    cursor.observed_at = observed_at
    event = db.get(AlertEvent, cursor.active_event_id) if cursor.active_event_id else None
    phase = None
    if event:
        event.last_value = value
        event.last_observed_at = observed_at
        event.version += 1
        if value <= rule.recover_value:
            event.status = "recovered"
            event.recovered_at = observed_at
            cursor.active_event_id = None
            phase = "recovered"
        else:
            event.occurrences += 1
    elif value >= rule.trigger_value:
        event = AlertEvent(
            rule_id=rule.id,
            station_id=station.id,
            operator_id=station.operator_id,
            target=target,
            opened_at=observed_at,
            last_observed_at=observed_at,
            last_value=value,
            rule_snapshot={
                "code": rule.code,
                "version": rule.version,
                "metric": rule.metric,
                "unit": rule.unit,
                "trigger": str(rule.trigger_value),
                "recover": str(rule.recover_value),
                "notifications_enabled": rule.notifications_enabled,
            },
        )
        db.add(event)
        db.flush()
        cursor.active_event_id = event.id
        phase = "opened"
    if phase:
        emit(
            db,
            event,
            rule,
            phase,
            f"演示告警：{rule.name} · {'触发' if phase == 'opened' else '恢复'} · {target}",
        )
    return {
        "outcome": phase or ("merged" if event else "normal"),
        "event_id": event.id if event else None,
    }


def access(db, user, event_id, lock=False):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "无权访问告警")
    stmt = scoped_records(select(AlertEvent), AlertEvent, user).where(AlertEvent.id == event_id)
    if user.role == "operator":
        stmt = stmt.where(AlertEvent.operator_id == user.operator_id)
    event = db.scalar(stmt.with_for_update() if lock else stmt)
    if not event:
        raise HTTPException(404, "告警不存在或不可访问")
    return event


def output(e):
    return {
        "id": e.id,
        "configuration_evidence": e.configuration_evidence,
        "station_id": e.station_id,
        "target": e.target,
        "status": e.status,
        "opened_at": e.opened_at.isoformat(),
        "last_observed_at": e.last_observed_at.isoformat(),
        "recovered_at": e.recovered_at.isoformat() if e.recovered_at else None,
        "occurrences": e.occurrences,
        "last_value": str(e.last_value),
        "rule_snapshot": e.rule_snapshot,
        "acknowledged_at": e.acknowledged_at.isoformat() if e.acknowledged_at else None,
        "acknowledge_note": e.acknowledge_note,
        "version": e.version,
        "is_demo": e.is_demo,
    }
