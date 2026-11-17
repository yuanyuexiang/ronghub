"""Seed explicit synthetic device history for one owned localhost browser station."""

import argparse
import json
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.engine import make_url

from app import subsidy_device_days, subsidy_device_scopes
from app.config import settings
from app.db import SessionLocal
from app.models import Equipment, RatingAction, RatingApplication, RatingStandard, Station, User


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station", required=True, type=int)
    parser.add_argument("--container-preview", action="store_true")
    args = parser.parse_args()
    if not settings.demo_mode or (
        make_url(settings.database_url).host not in {"localhost", "127.0.0.1"}
        and not (args.container_preview and make_url(settings.database_url).host == "postgres")
    ):
        raise SystemExit("Device history fixtures require a local demo database")
    with SessionLocal.begin() as db:
        station = db.scalar(select(Station).where(Station.id == args.station).with_for_update())
        owner = db.scalar(select(User).where(User.username == "operator_a"))
        if (
            not station
            or not owner
            or station.operator_id != owner.operator_id
            or station.source != "browser-test"
            or not station.name.startswith("浏览器验收-")
            or station.status != "operating"
        ):
            raise SystemExit("Refusing station outside owned operating browser fixtures")
        if db.scalar(select(Equipment.id).where(Equipment.station_id == station.id)) or db.scalar(
            select(RatingApplication.id).where(RatingApplication.station_id == station.id)
        ):
            raise SystemExit("History fixture requires a fresh browser station")
        standard = db.scalar(
            select(RatingStandard).where(RatingStandard.is_demo.is_(True)).limit(1)
        )
        actor = db.scalar(select(User).where(User.username == "association"))
        if not standard or not actor:
            raise SystemExit("Seed demo users and rating standard first")
        db.add(
            Equipment(
                station_id=station.id, external_id="browser-device", name="演示设备", power_kw=120
            )
        )
        published = datetime(2026, 10, 1, tzinfo=UTC)
        grade = RatingApplication(
            station_id=station.id,
            operator_id=station.operator_id,
            standard_id=standard.id,
            status="published",
            version=4,
            initial_scores={},
            current_scores={},
            calculation={"stars": 5, "total": "96", "missing": []},
            rule_snapshot={},
            evidence_note="仅用于本地浏览器验证的合成历史，非真实评星结果",
            published_at=published,
            valid_until=date(2026, 12, 31),
            is_demo=True,
        )
        db.add(grade)
        db.flush()
        grade.report_snapshot = {
            "report_id": f"RATE-{grade.id}-V4",
            "station": {"id": station.id},
            "published_at": published.isoformat(),
            "valid_until": "2026-12-31",
            "calculation": {"stars": 5},
            "is_demo": True,
        }
        db.add(
            RatingAction(
                application_id=grade.id,
                actor_id=actor.id,
                action="publish",
                comment="Synthetic browser-only publication history",
                created_at=published + timedelta(seconds=1),
                transition_snapshot={
                    "before": None,
                    "after": {
                        "station_id": station.id,
                        "operator_id": station.operator_id,
                        "status": "published",
                        "version": 4,
                        "stars": 5,
                        "published_at": published.isoformat(),
                        "valid_until": "2026-12-31",
                        "is_demo": True,
                    },
                },
            )
        )
        for day, kwh in [(1, "42.125000"), (2, "0")]:
            day_text = f"2026-11-0{day}"
            scope = f"browser-scope-{day}"
            updated = "2026-11-05T01:00:00+08:00"
            subsidy_device_scopes.ingest(
                db,
                station.source,
                station.operator_id,
                station,
                [
                    {
                        "external_id": scope,
                        "day": day_text,
                        "equipment_external_ids": ["browser-device"],
                        "complete": True,
                        "source_updated_at": updated,
                    }
                ],
                is_demo=True,
            )
            subsidy_device_days.ingest(
                db,
                station.source,
                station.operator_id,
                station,
                [
                    {
                        "external_id": f"browser-fact-{day}",
                        "scope_external_id": scope,
                        "equipment_external_id": "browser-device",
                        "day": day_text,
                        "equipment_name": "演示设备",
                        "equipment_type": "dc",
                        "power_kw": "120",
                        "production_date": "2025-01-01",
                        "business_kind": "charging",
                        "energy_kwh": kwh,
                        "energy_complete": True,
                        "metric_basis": "calendar_day_seconds_v1",
                        "fault_observed_seconds": "86400",
                        "fault_unknown_seconds": "0",
                        "fault_seconds": "0",
                        "offline_observed_seconds": "86400",
                        "offline_unknown_seconds": "0",
                        "offline_seconds": "0",
                        "source_updated_at": updated,
                    }
                ],
                is_demo=True,
            )
    print(json.dumps({"station_id": args.station, "synthetic_history": True}))


if __name__ == "__main__":
    main()
