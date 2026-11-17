import secrets
from datetime import timedelta

import pytest
from sqlalchemy import delete, func, select

from app.db import SessionLocal
from app.models import (
    AlertPeriodFact,
    ChargingOrder,
    Connector,
    Equipment,
    FaultEvent,
    HourlyEnergyRecord,
    Operator,
    RuntimeInterval,
    Station,
    SubsidyDeviceDay,
    SubsidyDeviceScope,
    SyncTask,
    now,
)
from app.sync import DemoAdapter, claim, enqueue, execute


@pytest.fixture()
def operator():
    with SessionLocal.begin() as db:
        op = Operator(name="sync-test-" + secrets.token_hex(8))
        db.add(op)
        db.flush()
        oid = op.id
    yield oid
    with SessionLocal.begin() as db:
        stations = select(Station.id).where(Station.operator_id == oid)
        equipment = select(Equipment.id).where(Equipment.station_id.in_(stations))
        db.execute(delete(AlertPeriodFact).where(AlertPeriodFact.operator_id == oid))
        db.execute(delete(SubsidyDeviceDay).where(SubsidyDeviceDay.operator_id == oid))
        db.execute(delete(SubsidyDeviceScope).where(SubsidyDeviceScope.operator_id == oid))
        db.execute(delete(Connector).where(Connector.equipment_id.in_(equipment)))
        db.execute(delete(Equipment).where(Equipment.station_id.in_(stations)))
        db.execute(delete(RuntimeInterval).where(RuntimeInterval.operator_id == oid))
        db.execute(delete(FaultEvent).where(FaultEvent.operator_id == oid))
        db.execute(delete(HourlyEnergyRecord).where(HourlyEnergyRecord.operator_id == oid))
        db.execute(delete(ChargingOrder).where(ChargingOrder.operator_id == oid))
        db.execute(delete(Station).where(Station.operator_id == oid))
        db.execute(delete(SyncTask).where(SyncTask.operator_id == oid))
        db.execute(delete(Operator).where(Operator.id == oid))


def test_idempotency_and_preserve_business_status(operator):
    key = secrets.token_hex(10)
    with SessionLocal.begin() as db:
        task = enqueue(db, operator, key)
        tid = task.id
        assert enqueue(db, operator, key).id == tid
    first = claim(tid)
    assert first[0] == tid
    assert execute(first)
    with SessionLocal.begin() as db:
        station = db.scalar(select(Station).where(Station.operator_id == operator))
        station.status = "operating"
        assert db.get(SyncTask, tid).status == "succeeded"
        next_tid = enqueue(db, operator, secrets.token_hex(10)).id
    assert execute(claim(next_tid))
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count()).select_from(Station).where(Station.operator_id == operator)
            )
            == 1
        )
        station = db.scalar(select(Station).where(Station.operator_id == operator))
        assert station.status == "operating"
        assert (
            len(list(db.scalars(select(Equipment).where(Equipment.station_id == station.id)))) == 1
        )
        eid = db.scalar(select(Equipment.id).where(Equipment.station_id == station.id))
        assert (
            db.scalar(
                select(func.count()).select_from(Connector).where(Connector.equipment_id == eid)
            )
            == 2
        )


def test_retry_and_expired_lease_recovery(operator):
    class Broken:
        def fetch(self, operator_id):
            raise TimeoutError("sensitive upstream detail must not persist")

    with SessionLocal.begin() as db:
        tid = enqueue(db, operator, secrets.token_hex(10)).id
    assert not execute(claim(tid), Broken())
    with SessionLocal.begin() as db:
        t = db.get(SyncTask, tid)
        assert t.status == "retry" and "sensitive" not in t.error
        t.available_at = now() - timedelta(seconds=1)
    old = claim(tid)
    with SessionLocal.begin() as db:
        db.get(SyncTask, tid).lease_until = now() - timedelta(seconds=1)
    new = claim(tid)
    assert old[1] != new[1]
    assert not execute(old, DemoAdapter())
    assert execute(new, DemoAdapter())
    with SessionLocal() as db:
        assert db.get(SyncTask, tid).attempts == 3
        assert db.get(SyncTask, tid).status == "succeeded"


def test_lease_renewal_rejects_expired_and_previous_owner(operator):
    from app.sync import renew

    with SessionLocal.begin() as db:
        tid = enqueue(db, operator, secrets.token_hex(8)).id
    claimed = claim(tid)
    assert renew(tid, claimed[1])
    assert not renew(tid, "wrong-owner")
    with SessionLocal.begin() as db:
        db.get(SyncTask, tid).lease_until = now() - timedelta(seconds=1)
    assert not renew(tid, claimed[1])
    replacement = claim(tid)
    assert replacement[1] != claimed[1]
    assert not renew(tid, claimed[1])
    assert renew(tid, replacement[1])
    assert execute(replacement)
    assert not renew(tid, replacement[1])


def test_heartbeat_keeps_fetch_alive_beyond_initial_lease(operator, monkeypatch):
    import time

    from app import sync

    with SessionLocal.begin() as db:
        tid = enqueue(db, operator, secrets.token_hex(8)).id
    claimed = claim(tid)
    with SessionLocal.begin() as db:
        db.get(SyncTask, tid).lease_until = now() + timedelta(seconds=0.2)
    heartbeat = sync.lease_heartbeat
    monkeypatch.setattr(
        sync, "lease_heartbeat", lambda tid, token: heartbeat(tid, token, interval=0.02)
    )

    class SlowAdapter:
        def fetch(self, oid):
            time.sleep(0.35)
            return DemoAdapter().fetch(oid)

    assert execute(claimed, SlowAdapter())
    with SessionLocal() as db:
        task = db.get(SyncTask, tid)
        assert task.status == "succeeded" and task.lease_owner is None


def test_source_coordinate_validation_and_omission_preservation(operator):
    from app import location
    from app.sync import ingest

    record = DemoAdapter().fetch(operator)[0]
    with SessionLocal.begin() as db:
        ingest(db, "demo", operator, [record])
        station = db.scalar(select(Station).where(Station.operator_id == operator))
        assert station.coordinate_system == "wgs84" and station.location_is_demo
        original = station.longitude
        omitted = {
            key: value
            for key, value in record.items()
            if key
            not in {
                "longitude",
                "latitude",
                "coordinate_system",
                "location_is_demo",
                "usage_type",
                "construction_site",
                "site_state",
                "source_sync_state",
                "source_first_connected_at",
            }
        }
        ingest(db, "demo", operator, [omitted])
        db.refresh(station)
        assert station.longitude == original
        assert station.usage_type == "public" and station.site_state == "normal"
        cleared = {**record, "longitude": None, "latitude": None, "coordinate_system": "unknown"}
        ingest(db, "demo", operator, [cleared])
        db.refresh(station)
        assert station.longitude is None and station.latitude is None
    for patch in [
        {"longitude": "NaN"},
        {"latitude": "91"},
        {"coordinate_system": "guessed"},
        {"longitude": None},
    ]:
        with pytest.raises(ValueError):
            location.values({**record, **patch})
    with pytest.raises(ValueError):
        location.values({"longitude": "119"})
