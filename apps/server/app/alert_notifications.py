"""Atomic local routing intents; this module contains no SMS network provider."""

from sqlalchemy import select, text

from app.config import settings
from app.models import (
    AlertNotice,
    AlertNotificationPreference,
    AlertNotificationRouting,
    AlertRecipient,
    AlertSmsDelivery,
)

LOCK = 78230013


def lock(db, write=False):
    db.execute(
        text(
            f"SELECT {'pg_advisory_xact_lock' if write else 'pg_advisory_xact_lock_shared'}({LOCK})"
        )
    )


def category_for(rule):
    kind = (rule.configuration or {}).get("kind")
    if kind in {"station_utilization", "station_fault", "station_offline"}:
        return "station_metric"
    if kind in {"device_temperature", "device_current", "device_leakage"}:
        return "device_charging"
    # Only the existing explicit demo metric is classified; never guess from a display name.
    if rule.configuration is None and rule.metric == "offline_minutes":
        return "device_offline"
    return None


def emit(db, event, rule, phase, message):
    lock(db)
    previous = db.scalar(
        select(AlertNotificationRouting).where(
            AlertNotificationRouting.event_id == event.id, AlertNotificationRouting.phase == phase
        )
    )
    if previous:
        return previous
    category = category_for(rule)
    preference = (
        db.get(AlertNotificationPreference, category, populate_existing=True) if category else None
    )
    in_app = rule.notifications_enabled and (preference.in_app_enabled if preference else True)
    sms = bool(preference and preference.sms_enabled)
    notice = None
    if in_app:
        notice = AlertNotice(event_id=event.id, phase=phase, message=message)
        db.add(notice)
        db.flush()
    recipients = (
        list(
            db.scalars(
                select(AlertRecipient)
                .where(AlertRecipient.enabled.is_(True), AlertRecipient.deleted.is_(False))
                .order_by(AlertRecipient.id)
            )
        )
        if sms
        else []
    )
    recipients = [row for row in recipients if category in row.categories]
    status = (
        "not_requested"
        if not sms
        else "no_recipients"
        if not recipients
        else "simulated"
        if settings.demo_mode and settings.sms_mode == "demo"
        else "provider_disabled"
    )
    routing = AlertNotificationRouting(
        event_id=event.id,
        phase=phase,
        category=category,
        preference_version=preference.version if preference else None,
        in_app_enabled=in_app,
        sms_enabled=sms,
        sms_status=status,
        notice_id=notice.id if notice else None,
    )
    db.add(routing)
    db.flush()
    for recipient in recipients:
        db.add(
            AlertSmsDelivery(
                routing_id=routing.id,
                recipient_id=recipient.id,
                recipient_version=recipient.version,
                name_snapshot=recipient.name,
                phone_snapshot=recipient.phone,
                status=status,
            )
        )
    return routing
