"""Independent synthetic periods for a fresh, explicitly owned localhost browser station."""

import argparse

from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.alert_period_demo import records
from app.alert_period_facts import ingest
from app.config import settings
from app.db import SessionLocal
from app.models import AlertPeriodFact, Equipment, Station, User


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station", type=int, required=True)
    args = parser.parse_args()
    if not settings.demo_mode or make_url(settings.database_url).host not in {
        "localhost",
        "127.0.0.1",
    }:
        raise SystemExit("Period fixtures require a local demo database")
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
            raise SystemExit("Refusing station outside owned browser fixtures")
        if db.scalar(select(Equipment.id).where(Equipment.station_id == station.id)) or db.scalar(
            select(AlertPeriodFact.id).where(AlertPeriodFact.station_id == station.id)
        ):
            raise SystemExit("Period fixture requires a fresh browser station")
        db.add(
            Equipment(
                station_id=station.id, external_id="equipment-1", name="演示历史设备", power_kw=10
            )
        )
        db.flush()
        payloads = records(station.operator_id)[:2]
        for payload in payloads:
            payload["members"][0]["equipment_name"] = "演示历史设备"
        payloads[1]["members"][0]["unknown_seconds"] = "1"
        ingest(
            db,
            station_id=station.id,
            operator_id=station.operator_id,
            source=station.source,
            records=payloads,
        )
    print("Seeded only synthetic coverage for the owned browser station.")


if __name__ == "__main__":
    main()
