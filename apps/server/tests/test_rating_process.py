# ruff: noqa: F811 - imported pytest fixtures
import csv
import io

from sqlalchemy import select
from test_business_exports import login, report_staff  # noqa: F401
from test_rating import rating_fixture  # noqa: F401

from app import rating
from app.db import SessionLocal
from app.models import RatingAction, RatingApplication, User


def test_review_process_pages_frozen_events_without_using_current_result(
    rating_fixture, accounts, report_staff
):
    uid, station, standard = rating_fixture
    names, password, _ = accounts
    with SessionLocal.begin() as db:
        owner = db.get(User, uid)
        application = rating.create(db, owner, station, standard, {"service": "80"}, "")
        aid = application.id
        actor = db.scalar(select(User).where(User.username == report_staff[0][1]))
        for index in range(23):
            db.add(
                RatingAction(
                    application_id=aid,
                    actor_id=actor.id,
                    action="review",
                    comment=f"review-{index}",
                    payload={},
                    transition_snapshot={
                        "before": {"stars": 1, "total_score": "65.00", "status": "submitted"},
                        "after": {"stars": 3, "total_score": "85.00", "status": "reviewed"},
                    },
                )
            )
        db.add(
            RatingAction(
                application_id=aid,
                actor_id=actor.id,
                action="publish",
                comment="legacy",
                payload={},
                transition_snapshot=None,
            )
        )
        db.get(RatingApplication, aid).calculation = {"total": "99.00", "stars": 5}
    client = login(report_staff[0][1], password)
    first = client.get(f"/api/v1/ratings/process?record_id={aid}&action=review&page_size=20")
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "no-store"
    data = first.json()
    assert data["total"] == 23 and len(data["items"]) == 20
    assert data["items"][0]["before"]["stars"] == 1
    assert data["items"][0]["after"]["stars"] == 3
    assert data["items"][0]["after"]["total_score"] == "85.00"
    assert data["items"][0]["actor_username"] == report_staff[0][1]
    page = client.get(f"/api/v1/ratings/process?record_id={aid}&action=review&page=2").json()
    assert len(page["items"]) == 3
    assert not {row["id"] for row in data["items"]} & {row["id"] for row in page["items"]}
    legacy = client.get(f"/api/v1/ratings/process?record_id={aid}&action=publish").json()["items"][
        0
    ]
    assert (
        legacy["snapshot_recorded"] is False
        and legacy["before"] is None
        and legacy["after"] is None
    )
    exported = client.get(f"/api/v1/ratings/process/export?record_id={aid}&action=review")
    assert exported.status_code == 200
    rows = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))
    assert len(rows) == 23
    assert {row["变更后星级"] for row in rows} == {"3"}
    assert {row["动作"] for row in rows} == {"review"}
    outsider = login(names[1], password)
    assert outsider.get(f"/api/v1/ratings/process?record_id={aid}").json()["total"] == 0
    assert client.get("/api/v1/ratings/process?action=invalid").status_code == 422
    assert client.get("/api/v1/ratings/process?page_size=101").status_code == 422
