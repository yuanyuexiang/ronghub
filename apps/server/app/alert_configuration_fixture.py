"""Remove unused configured-rule versions owned by localhost browser scenarios only."""

import argparse
import re

from sqlalchemy import delete, select
from sqlalchemy.engine import make_url

from app.config import settings
from app.db import SessionLocal
from app.models import AlertCursor, AlertEvent, AlertRule, Audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code", required=True)
    args = parser.parse_args()
    if (
        not settings.demo_mode
        or make_url(settings.database_url).host not in {"localhost", "127.0.0.1"}
        or not re.fullmatch(r"browser-alertcfg-[0-9a-f]{16}", args.code)
    ):
        raise SystemExit("Refusing nonlocal or unowned alert fixture")
    with SessionLocal.begin() as db:
        rows = list(
            db.scalars(select(AlertRule).where(AlertRule.code == args.code).with_for_update())
        )
        ids = [row.id for row in rows]
        if (
            any(not row.is_demo or row.configuration is None for row in rows)
            or db.scalar(select(AlertEvent.id).where(AlertEvent.rule_id.in_(ids)).limit(1))
            or db.scalar(select(AlertCursor.id).where(AlertCursor.rule_id.in_(ids)).limit(1))
        ):
            raise SystemExit("Refusing referenced or nonconfigured rules")
        db.execute(
            delete(Audit).where(
                Audit.object_id.in_(ids), Audit.action.like("alerts.configuration.%")
            )
        )
        db.execute(delete(AlertRule).where(AlertRule.id.in_(ids)))
    print("Removed only unused local configured browser rule versions.")


if __name__ == "__main__":
    main()
