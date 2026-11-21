# ruff: noqa: F811
import csv
import io
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from subsidy_samples import create_claim as sample_claim
from test_business_exports import login, report_staff  # noqa: F401
from test_subsidy import finance  # noqa: F401

from app import subsidy
from app.db import SessionLocal
from app.models import EnergyRecord, Station, SubsidyAction, User


def test_ledger_uses_finalized_records_and_beijing_disbursement_days(
    finance, accounts, report_staff
):
    uid, sid, pid = finance
    names, password, _ = accounts
    with SessionLocal.begin() as db:
        owner = db.get(User, uid)
        reviewer = db.scalar(select(User).where(User.username == report_staff[0][0]))
        db.get(Station, sid).name = "=ledger-formula"
        draft = sample_claim(db, owner, sid, pid, date(2026, 11, 1), date(2026, 11, 2))
        draft_id = draft.id
        for day in (3, 4):
            db.add(
                EnergyRecord(
                    station_id=sid,
                    source="test-subsidy",
                    external_id=f"ledger-{pid}-{day}",
                    day=date(2026, 11, day),
                    kwh=Decimal("10.00"),
                )
            )
        db.flush()
        item = sample_claim(db, owner, sid, pid, date(2026, 11, 3), date(2026, 11, 4))
        cid = item.id
        subsidy.act(db, owner, cid, "submit", 1, "")
        subsidy.act(db, reviewer, cid, "approve", 2, "approved")
        subsidy.act(db, reviewer, cid, "record_disbursement", 3, "registration, not payment proof")
        actions = db.scalars(select(SubsidyAction).where(SubsidyAction.claim_id == cid)).all()
        for action in actions:
            if action.action == "approve":
                action.created_at = datetime(2026, 11, 2, 16, 0, tzinfo=UTC)
            elif action.action == "record_disbursement":
                action.created_at = datetime(2026, 11, 4, 16, 0, tzinfo=UTC)
    client = login(names[0], password)
    path = "/api/v1/subsidy/ledger"
    result = client.get(path, params={"q": "=ledger-formula"})
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "no-store"
    data = result.json()
    assert data["total"] == 1
    row = data["items"][0]
    assert row["id"] == cid and row["id"] != draft_id and row["generated_at"] is None
    assert row["approved_at"].startswith("2026-11-02T16:00")
    assert (
        client.get(path, params={"paid_from": "2026-11-05", "paid_to": "2026-11-05"}).json()[
            "total"
        ]
        == 1
    )
    assert client.get(path, params={"paid_to": "2026-11-04"}).json()["total"] == 0
    assert client.get(path, params={"status": "approved"}).json()["total"] == 0
    assert client.get(path, params={"status": "draft"}).status_code == 422
    assert client.get(path, params={"paid_to": "9999-12-31"}).status_code == 422
    assert (
        client.get(path, params={"paid_from": "2026-11-06", "paid_to": "2026-11-05"}).status_code
        == 422
    )
    assert login(names[1], password).get(path).json()["total"] == 0
    assert login(report_staff[0][1], password).get(path).status_code == 403
    exported = client.get(
        path + "/export", params={"q": "=ledger-formula", "paid_from": "2026-11-05"}
    )
    assert exported.status_code == 200, exported.text
    records = list(csv.reader(io.StringIO(exported.content.decode("utf-8-sig"))))
    assert len(records) == 2 and records[1][1].startswith("'=")
    assert (
        login(names[1], password).get(path + "/export", params={"q": "=ledger-formula"}).status_code
        == 200
    )
