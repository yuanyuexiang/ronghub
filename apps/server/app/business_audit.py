"""Minimal business transitions, committed atomically with their domain records.

Never include free text, uploaded content, credentials or complete request bodies.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.models import Audit

STATE_FIELDS = {
    "station_id",
    "operator_id",
    "policy_id",
    "standard_id",
    "parent_id",
    "kind",
    "status",
    "version",
    "station_status",
    "station_version",
    "total_kwh",
    "base_amount",
    "adjusted_amount",
    "total_score",
    "stars",
    "valid_until",
    "published_at",
    "is_demo",
    "start_date",
    "end_date",
    "due_at",
    "severity",
    "audience",
    "acknowledged_at",
    "acknowledged_by",
    "rule_id",
    "replacement_id",
}


def numeric_scores(raw):
    if not isinstance(raw, dict):
        return {}
    result = {}
    for key, value in raw.items():
        if not isinstance(key, str) or len(key) > 100:
            continue
        if value is None:
            result[key] = None
            continue
        if isinstance(value, bool):
            continue
        try:
            number = Decimal(str(value))
        except (ValueError, InvalidOperation):
            continue
        if number.is_finite() and len(str(value)) <= 100:
            result[key] = str(value)
    return result


def snapshot(raw):
    result = {}
    for key, value in raw.items():
        if key == "scores":
            result[key] = numeric_scores(value)
        elif key in STATE_FIELDS:
            if isinstance(value, (date, datetime)):
                result[key] = value.isoformat()
            elif isinstance(value, Decimal):
                result[key] = str(value)
            elif value is None or isinstance(value, (str, int, bool)):
                result[key] = value
    return result


def state(record):
    values = {key: getattr(record, key) for key in STATE_FIELDS if hasattr(record, key)}
    if hasattr(record, "current_scores"):
        values["scores"] = record.current_scores
        result = record.calculation or {}
        values.update(total_score=result.get("total"), stars=result.get("stars"))
    return snapshot(values)


def transition(db, user, action, object_id, before, after, object_type):
    db.add(
        Audit(
            user_id=user.id,
            action=action,
            object_id=object_id,
            detail={
                "object_type": object_type,
                "before": snapshot(before or {}),
                "after": snapshot(after or {}),
            },
        )
    )
