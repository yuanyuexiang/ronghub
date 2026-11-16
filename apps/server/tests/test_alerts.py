import secrets
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select

from app import alerts
from app.db import SessionLocal
from app.models import AlertCursor, AlertEvent, AlertNotice, AlertRule, User, now


@pytest.fixture()
def rule(accounts):
    names, _, stations = accounts
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.username == names[0]))
        reg = SimpleNamespace(id=user.id, role="regulator")
        r = AlertRule(
            code="test-" + secrets.token_hex(6),
            version=1,
            name="离线时长",
            metric="offline_minutes",
            unit="分钟",
            trigger_value=30,
            recover_value=5,
        )
        db.add(r)
        db.flush()
        rid = r.id
    yield reg, rid, stations, names
    with SessionLocal.begin() as db:
        events = select(AlertEvent.id).where(AlertEvent.rule_id == rid)
        db.execute(delete(AlertCursor).where(AlertCursor.rule_id == rid))
        db.execute(delete(AlertNotice).where(AlertNotice.event_id.in_(events)))
        db.execute(delete(AlertEvent).where(AlertEvent.rule_id == rid))
        db.execute(delete(AlertRule).where(AlertRule.id == rid))


def test_trigger_merge_stale_recovery_and_new_episode(rule):
    reg, rid, stations, _ = rule
    stamp = now() - timedelta(hours=1)
    with SessionLocal.begin() as db:

        def sample(seconds, value):
            return alerts.observe(
                db,
                reg,
                stations[0],
                rid,
                "connector-1",
                Decimal(value),
                stamp + timedelta(seconds=seconds),
            )

        assert sample(0, "0")["outcome"] == "normal"
        opened = sample(1, "30")
        eid = opened["event_id"]
        assert opened["outcome"] == "opened"
        assert sample(2, "40") == {"outcome": "merged", "event_id": eid}
        assert sample(2, "0")["outcome"] == "ignored_stale"
        assert sample(1, "0")["outcome"] == "ignored_stale"
        assert sample(3, "10")["outcome"] == "merged"
        assert sample(4, "5")["outcome"] == "recovered"
        assert sample(5, "0")["outcome"] == "normal"
        db.flush()
        event = db.get(AlertEvent, eid)
        assert event.status == "recovered" and event.occurrences == 3
        assert (
            db.scalar(
                select(func.count()).select_from(AlertNotice).where(AlertNotice.event_id == eid)
            )
            == 2
        )
        new = sample(6, "31")
        assert new["outcome"] == "opened" and new["event_id"] != eid
        db.get(AlertRule, rid).notifications_enabled = False
        assert sample(7, "0")["outcome"] == "recovered"
        db.flush()
        assert (
            db.scalar(
                select(func.count())
                .select_from(AlertNotice)
                .where(AlertNotice.event_id == new["event_id"])
            )
            == 1
        )


def test_sample_permissions_isolation_and_time(rule):
    reg, rid, stations, names = rule
    with SessionLocal.begin() as db:
        owner = db.scalar(select(User).where(User.username == names[0]))
        other = db.scalar(select(User).where(User.username == names[1]))
        with pytest.raises(HTTPException) as e:
            alerts.observe(db, owner, stations[0], rid, "test", Decimal(40), now())
        assert e.value.status_code == 403
        with pytest.raises(HTTPException) as e:
            alerts.observe(
                db, reg, stations[0], rid, "test", Decimal(40), now() + timedelta(hours=1)
            )
        assert e.value.status_code == 422
        result = alerts.observe(db, reg, stations[0], rid, "test", Decimal(40), now())
        assert alerts.access(db, owner, result["event_id"]).station_id == stations[0]
        with pytest.raises(HTTPException) as e:
            alerts.access(db, other, result["event_id"])
        assert e.value.status_code == 404


def test_concurrent_repeated_samples_create_one_episode(rule):
    from concurrent.futures import ThreadPoolExecutor

    reg, rid, stations, _ = rule
    stamp = now()

    def ingest(_):
        with SessionLocal.begin() as db:
            return alerts.observe(db, reg, stations[0], rid, "same-sample", Decimal(40), stamp)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(ingest, range(2)))
    assert sorted(r["outcome"] for r in results) == ["ignored_stale", "opened"]
    with SessionLocal() as db:
        assert (
            db.scalar(select(func.count()).select_from(AlertEvent).where(AlertEvent.rule_id == rid))
            == 1
        )
        ids = select(AlertEvent.id).where(AlertEvent.rule_id == rid)
        assert (
            db.scalar(
                select(func.count()).select_from(AlertNotice).where(AlertNotice.event_id.in_(ids))
            )
            == 1
        )
