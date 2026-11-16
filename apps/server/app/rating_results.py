"""Published rating views and immutable report snapshots; no public/anonymous access policy."""

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.models import Operator, RatingAction, RatingApplication, Station, User, now


def local_day(at: datetime | None = None):
    return (at or now()).astimezone(ZoneInfo("Asia/Shanghai")).date()


def latest(db, station_id):
    return db.scalar(
        select(RatingApplication)
        .where(
            RatingApplication.station_id == station_id,
            RatingApplication.status.in_(["published", "delisted"]),
        )
        .order_by(
            func.coalesce(RatingApplication.published_at, RatingApplication.created_at).desc(),
            RatingApplication.id.desc(),
        )
        .limit(1)
    )


def effective(a, today=None, station_status=None):
    if a.status == "delisted":
        state, stars = "delisted", 0
    elif a.status == "superseded":
        state, stars = "superseded", 0
    elif a.status in {"submitted", "reviewed"}:
        state, stars = "evaluating", None
    elif a.status != "published":
        state, stars = "unpublished", None
    elif station_status is not None and station_status not in {
        "access_approved",
        "accepted",
        "operating",
    }:
        state, stars = "inactive", 0
    elif a.valid_until is None:
        state, stars = "validity_unknown", None
    elif a.valid_until < (today or local_day()):
        state, stars = "expired", 0
    else:
        state, stars = "valid", a.calculation.get("stars")
    return {
        "result_state": state,
        "effective_stars": stars,
        "show_association_mark": state == "valid" and bool(stars),
    }


def reference(a):
    return {
        "id": a.id,
        "version": a.version,
        "status": a.status,
        "stars": a.calculation.get("stars"),
        "valid_until": a.valid_until.isoformat() if a.valid_until else None,
        **effective(a),
    }


def report(db, a, actor, published_at):
    station = db.get(Station, a.station_id)
    operator = db.get(Operator, a.operator_id)
    return {
        "report_id": f"RATE-{a.id}-V{a.version + 1}",
        "is_demo": True,
        "notice": "演示评价报告，非正式认证文件；运行数据与正式规则尚待确认",
        "kind": a.kind,
        "published_at": published_at.isoformat(),
        "published_by": actor.username if hasattr(actor, "username") else str(actor.id),
        "station": {
            "id": station.id,
            "name": station.name,
            "district": station.district,
            "address": station.address,
            "operator": operator.name,
        },
        "valid_until": a.valid_until.isoformat(),
        "before_result": a.before_result,
        "rule_snapshot": a.rule_snapshot,
        "initial_scores": a.initial_scores,
        "current_scores": a.current_scores,
        "calculation": a.calculation,
        "evidence_note": a.evidence_note,
        "history": [
            {
                "action": action.action,
                "comment": action.comment,
                "actor": db.get(User, action.actor_id).username,
                "created_at": action.created_at.isoformat(),
                "payload": action.payload,
            }
            for action in db.scalars(
                select(RatingAction)
                .where(RatingAction.application_id == a.id)
                .order_by(RatingAction.id)
            )
        ],
    }
