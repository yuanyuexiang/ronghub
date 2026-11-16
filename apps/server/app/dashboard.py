from decimal import Decimal

from sqlalchemy import func, select

from app.authorization import permissions
from app.models import (
    EnergyRecord,
    RatingApplication,
    StaffApplication,
    StationApplication,
    SubsidyClaim,
)


def summary(db, user, station_ids):
    def count(model, status):
        return (
            db.scalar(
                select(func.count())
                .select_from(model)
                .where(model.station_id.in_(station_ids), model.status == status)
            )
            or 0
        )

    granted = permissions(db, user)
    data = {}
    if user.role == "regulator" and user.allowed_districts is None and "accounts.manage" in granted:
        data["staff_applications_pending"] = db.scalar(
            select(func.count())
            .select_from(StaffApplication)
            .where(StaffApplication.status == "pending")
        )
    if "ratings.read" in granted:
        data.update(
            rating_pending=count(RatingApplication, "submitted"),
            rating_reviewed=count(RatingApplication, "reviewed"),
            rating_published=count(RatingApplication, "published"),
        )
    if user.role == "association":
        return data
    if "applications.read" in granted:
        data.update(
            applications_pending=count(StationApplication, "submitted"),
            applications_draft=count(StationApplication, "draft"),
            applications_rejected=count(StationApplication, "rejected"),
        )
    if "subsidy.read" in granted:
        data["subsidies_pending"] = count(SubsidyClaim, "submitted")
        data["subsidy_total"] = str(
            db.scalar(
                select(func.coalesce(func.sum(SubsidyClaim.adjusted_amount), 0)).where(
                    SubsidyClaim.station_id.in_(station_ids),
                    SubsidyClaim.status.in_(["approved", "disbursed"]),
                )
            )
            or Decimal(0)
        )
    if "statistics.read" in granted:
        total = db.scalar(
            select(func.sum(EnergyRecord.kwh)).where(
                EnergyRecord.station_id.in_(station_ids), EnergyRecord.eligible.is_(True)
            )
        )
        data["energy_total"] = str(total) if total is not None else None
    return data
