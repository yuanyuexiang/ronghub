"""Cleanup only notification children of explicitly owned test events."""

from sqlalchemy import delete, select

from app.models import AlertNotificationRouting, AlertSmsDelivery


def remove_for_events(db, events):
    routes = select(AlertNotificationRouting.id).where(
        AlertNotificationRouting.event_id.in_(events)
    )
    db.execute(delete(AlertSmsDelivery).where(AlertSmsDelivery.routing_id.in_(routes)))
    db.execute(
        delete(AlertNotificationRouting).where(AlertNotificationRouting.event_id.in_(events))
    )
