"""Local browser preference snapshot and exact owned-contact cleanup; never a business API."""

import argparse
import json
import os
import re
import secrets
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url

from app import alert_notifications
from app.alert_notification_contracts import CATEGORIES
from app.config import settings
from app.db import SessionLocal
from app.models import AlertNotificationPreference, AlertRecipient, AlertSmsDelivery, Audit, User

ROOT = Path(__file__).resolve().parents[3]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["snapshot", "restore"])
    p.add_argument("--category", choices=list(CATEGORIES), default="device_charging")
    p.add_argument("--token")
    p.add_argument("--version", type=int)
    p.add_argument("--marker")
    p.add_argument("--recipient", action="append", default=[])
    args = p.parse_args()
    if not settings.demo_mode or make_url(settings.database_url).host not in {
        "localhost",
        "127.0.0.1",
    }:
        raise SystemExit("Notification browser fixtures require a local demo database")
    if args.action == "snapshot":
        token = secrets.token_hex(16)
        path = ROOT / ".backups" / f"alert-notify-fixture-{token}.json"
        path.parent.mkdir(exist_ok=True)
        with SessionLocal.begin() as db:
            alert_notifications.lock(db, write=True)
            row = db.get(AlertNotificationPreference, args.category)
            values = (
                {
                    column.name: getattr(row, column.name)
                    for column in AlertNotificationPreference.__table__.columns
                }
                if row
                else None
            )
            if values:
                values["updated_at"] = values["updated_at"].isoformat()
            data = {
                "category": args.category,
                "previous": values,
                "audit_after": db.scalar(select(func.coalesce(func.max(Audit.id), 0))),
            }
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(data, stream)
        print(json.dumps({"token": token, "version": row.version if row else 0}))
        return
    if (
        not args.token
        or not re.fullmatch(r"[0-9a-f]{32}", args.token)
        or args.version is None
        or not args.marker
        or not re.fullmatch(r"browser-notify-[0-9a-f]{16}", args.marker)
    ):
        raise SystemExit("Refusing unowned notification fixture")
    path = ROOT / ".backups" / f"alert-notify-fixture-{args.token}.json"
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        data = json.loads(stream.read(65536))
    owned = {}
    for item in args.recipient:
        if not re.fullmatch(r"[1-9][0-9]{0,9}:[1-9][0-9]{0,9}", item):
            raise SystemExit("Invalid owned recipient/version pair")
        rid, version = map(int, item.split(":"))
        owned[rid] = version
    with SessionLocal.begin() as db:
        alert_notifications.lock(db, write=True)
        row = db.get(AlertNotificationPreference, data["category"])
        if (row.version if row else 0) != args.version:
            raise SystemExit("Refusing to overwrite changed notification preferences")
        admin = db.scalar(select(User).where(User.username == "regulator"))
        if not admin:
            raise SystemExit("No local fixture admin")
        for rid, version in owned.items():
            recipient = db.get(AlertRecipient, rid)
            if (
                not recipient
                or recipient.version != version
                or recipient.created_by != admin.id
                or not (recipient.name == args.marker or recipient.name == args.marker + "-edited")
            ):
                raise SystemExit("Refusing changed or unowned recipient")
        if db.scalar(
            select(AlertSmsDelivery.id).where(AlertSmsDelivery.recipient_id.in_(owned)).limit(1)
        ):
            raise SystemExit("Clean the owned browser event before its recipients")
        db.execute(
            delete(Audit).where(
                Audit.object_id.in_(owned), Audit.action.like("alerts.notification.recipient.%")
            )
        )
        db.execute(delete(AlertRecipient).where(AlertRecipient.id.in_(owned)))
        db.execute(
            delete(Audit).where(
                Audit.id > data["audit_after"],
                Audit.user_id == admin.id,
                Audit.action == "alerts.notification.preference",
                Audit.detail["after"]["category"].as_string() == data["category"],
            )
        )
        if data["previous"]:
            previous = data["previous"]
            previous["updated_at"] = datetime.fromisoformat(previous["updated_at"])
            if row:
                for key, value in previous.items():
                    setattr(row, key, value)
            else:
                db.add(AlertNotificationPreference(**previous))
        elif row:
            db.delete(row)
    path.unlink()
    print("Restored only the captured local preference and removed exact owned unused recipients.")


if __name__ == "__main__":
    main()
