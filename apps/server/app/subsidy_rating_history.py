"""Use recorded publication state as of a closed period, never today's effective tag."""

import hashlib
import json
from datetime import date, datetime, time, timedelta

from fastapi import HTTPException
from sqlalchemy import select

from app.hourly_energy import BEIJING
from app.models import RatingAction, RatingApplication
from app.subsidy_execution_contracts import SubsidyRatingReference


def at_period_end(db, station, end):
    cutoff = datetime.combine(end + timedelta(days=1), time.min, BEIJING)
    row = db.execute(
        select(RatingAction, RatingApplication)
        .join(RatingApplication, RatingApplication.id == RatingAction.application_id)
        .where(
            RatingApplication.station_id == station.id,
            RatingApplication.operator_id == station.operator_id,
            RatingAction.action.in_(["publish", "delist", "supersede"]),
            RatingAction.created_at < cutoff,
        )
        .order_by(RatingAction.created_at.desc(), RatingAction.id.desc())
        .limit(1)
    ).first()
    if not row:
        raise HTTPException(409, "周期结束时没有可验证的历史公示依据；未按无星补写")
    event, application = row
    after = (event.transition_snapshot or {}).get("after")
    report = application.report_snapshot
    try:
        if (
            event.action != "publish"
            or not isinstance(after, dict)
            or after.get("status") != "published"
            or not isinstance(report, dict)
        ):
            raise ValueError
        if (
            not isinstance(report.get("calculation"), dict)
            or not isinstance(report.get("station"), dict)
            or type(report["calculation"].get("stars")) is not int
            or type(after.get("version")) is not int
            or after["version"] < 1
        ):
            raise ValueError
        stars = after.get("stars")
        published = datetime.fromisoformat(after["published_at"])
        valid = date.fromisoformat(after["valid_until"])
        if (
            type(stars) is not int
            or not 0 <= stars <= 5
            or published.utcoffset() is None
            or published >= cutoff
            or valid < end
        ):
            raise ValueError
        if (
            after.get("station_id") != station.id
            or after.get("operator_id") != station.operator_id
            or not application.is_demo
            or after.get("is_demo") is not True
            or report.get("is_demo") is not True
        ):
            raise ValueError
        if (
            report.get("published_at") != after["published_at"]
            or report.get("valid_until") != after["valid_until"]
            or report.get("calculation", {}).get("stars") != stars
            or report.get("station", {}).get("id") != station.id
        ):
            raise ValueError
        if report.get("report_id") != f"RATE-{application.id}-V{after['version']}":
            raise ValueError
        result = SubsidyRatingReference(
            application_id=application.id,
            action_id=event.id,
            published_version=after["version"],
            report_id=report["report_id"],
            published_at=published.isoformat(),
            action_at=event.created_at.isoformat(),
            valid_until=valid.isoformat(),
            as_of=end.isoformat(),
            stars=stars,
            report_sha256=hashlib.sha256(
                json.dumps(report, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest(),
            is_demo=True,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(
            409, "历史公示已失效、依据不完整或口径不适用；未用当前星级替代"
        ) from error
    return result
