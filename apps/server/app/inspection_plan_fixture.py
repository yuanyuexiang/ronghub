"""Remove only a precisely identified locally owned browser plan before station fixture cleanup."""

import argparse
from uuid import UUID

from sqlalchemy import delete, select, update

from app.config import settings
from app.db import SessionLocal, engine
from app.models import Audit, InspectionPlan, InspectionTask, Station, User


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=int, required=True)
    parser.add_argument("--request-key", type=UUID, required=True)
    parser.add_argument("--stations", type=int, nargs="+", required=True)
    args = parser.parse_args()
    if not settings.demo_mode or engine.url.host not in {"localhost", "127.0.0.1"}:
        raise SystemExit("Browser cleanup requires the isolated local demo database")
    with SessionLocal.begin() as db:
        row = db.get(InspectionPlan, args.plan)
        if row is None:
            return
        if (
            row.request_key != str(args.request_key)
            or db.get(User, row.created_by).username != "regulator"
        ):
            raise SystemExit("Plan does not match this browser run")
        stations = list(db.scalars(select(Station).where(Station.id.in_(args.stations))))
        if len(stations) != len(set(args.stations)) or not all(
            station.source == "browser-test" and station.name.startswith("浏览器验收-")
            for station in stations
        ):
            raise SystemExit("All cleanup stations must be existing owned browser fixtures")
        if not {target["station_id"] for target in row.targets}.issubset(set(args.stations)):
            raise SystemExit("Plan contains targets outside this browser fixture")
        linked = list(
            db.scalars(select(InspectionTask.station_id).where(InspectionTask.plan_id == row.id))
        )
        if not set(linked).issubset(set(args.stations)):
            raise SystemExit("Plan tasks do not belong to this fixture")
        db.execute(
            update(InspectionTask).where(InspectionTask.plan_id == row.id).values(plan_id=None)
        )
        db.execute(
            delete(Audit).where(
                Audit.user_id == row.created_by,
                Audit.object_id == row.id,
                Audit.action.like("inspections.plan.%"),
            )
        )
        db.delete(row)
    print(
        "Owned browser plan detached and removed; station cleanup retains responsibility for its tasks/files."
    )


if __name__ == "__main__":
    main()
