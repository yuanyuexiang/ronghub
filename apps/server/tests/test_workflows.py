import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.db import SessionLocal
from app.models import ApprovalAction, Station, User
from app.workflows import act, create


def test_access_acceptance_commission_and_conflict(accounts):
    names, _, stations = accounts
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.username == names[0]))
        app = create(db, user, stations[0], "access", {"proof": "演示材料"})
        aid = app.id
        act(db, user, aid, "submit", 1, "")
        with pytest.raises(HTTPException) as e:
            act(db, user, aid, "approve", 2, "越权")
        assert e.value.status_code == 403

    # transient role-shaped actor avoids creating unrelated seeded accounts.
    class Regulator:
        role = "regulator"

    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.username == names[0]))
        regulator = Regulator()
        regulator.id = user.id
        act(db, regulator, aid, "approve", 2, "演示核验通过")
        assert db.get(Station, stations[0]).status == "access_approved"
        with pytest.raises(HTTPException) as e:
            act(db, regulator, aid, "approve", 2, "重复审核")
        assert e.value.status_code == 409
        acceptance = create(db, user, stations[0], "acceptance", {})
        act(db, user, acceptance.id, "submit", 1, "")
        act(db, regulator, acceptance.id, "approve", 2, "验收通过（演示）")
        commissioning = create(db, user, stations[0], "commission", {})
        act(db, user, commissioning.id, "submit", 1, "")
        act(db, regulator, commissioning.id, "approve", 2, "投运通过（演示）")
        assert db.get(Station, stations[0]).status == "operating"
        assert (
            len(
                list(db.scalars(select(ApprovalAction).where(ApprovalAction.application_id == aid)))
            )
            == 3
        )


def test_foreign_station_and_duplicate_application(accounts):
    names, _, stations = accounts
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.username == names[0]))
        with pytest.raises(HTTPException) as e:
            create(db, user, stations[1], "access", {})
        assert e.value.status_code == 404
        app = create(db, user, stations[0], "access", {})
        with pytest.raises(HTTPException) as e:
            create(db, user, stations[0], "access", {})
        assert e.value.status_code == 409
        act(db, user, app.id, "submit", 1, "")
        act(db, user, app.id, "withdraw", 2, "撤回演示申请")
        assert app.status == "withdrawn"
