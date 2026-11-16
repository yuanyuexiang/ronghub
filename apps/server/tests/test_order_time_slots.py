import csv
import io
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app import hourly_energy, orders
from app.db import SessionLocal
from app.main import app
from app.models import HourlyEnergyRecord, RoleProfile, Station, User

STAMP = datetime(2026, 11, 1, tzinfo=UTC)


def sample(stamp=STAMP, **patch):
    return {
        "meter_scope": "station",
        "started_at": stamp,
        "ended_at": stamp + timedelta(hours=1),
        "kwh": "0.100000",
        "source_updated_at": stamp + timedelta(hours=2),
        **patch,
    }


def test_hourly_revision_alignment_constraints_batch_and_concurrency(accounts):
    sid = accounts[2][0]

    def import_one():
        with SessionLocal.begin() as db:
            station = db.get(Station, sid)
            return hourly_energy.ingest(
                db, "test", station.operator_id, station, [sample()], is_demo=True
            )["changed"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: import_one(), range(2))) == [0, 1]
    with SessionLocal.begin() as db:
        station = db.get(Station, sid)
        assert (
            hourly_energy.ingest(
                db,
                "test",
                station.operator_id,
                station,
                [sample(source_updated_at=STAMP)],
                is_demo=True,
            )["ignored_stale"]
            == 1
        )
        with pytest.raises(ValueError, match="conflicting"):
            hourly_energy.ingest(
                db, "test", station.operator_id, station, [sample(kwh="0.2")], is_demo=True
            )
        assert (
            hourly_energy.ingest(
                db,
                "test",
                station.operator_id,
                station,
                [sample(kwh="0.2", source_updated_at=STAMP + timedelta(hours=3))],
                is_demo=True,
            )["changed"]
            == 1
        )
        row = db.scalar(select(HourlyEnergyRecord).where(HourlyEnergyRecord.station_id == sid))
        assert row.version == 2 and str(row.kwh) == "0.200000"
        with pytest.raises(ValueError, match="ownership"):
            hourly_energy.ingest(
                db, "wrong-source", station.operator_id, station, [sample()], is_demo=True
            )
        for patch in [
            {"started_at": datetime(2026, 11, 1)},
            {"started_at": STAMP + timedelta(minutes=1)},
            {"ended_at": STAMP + timedelta(minutes=30)},
            {"kwh": "NaN"},
            {"kwh": "-1"},
            {"meter_scope": "equipment"},
            {"equipment_external_id": "unexpected"},
            {"driver_phone": "private"},
        ]:
            with pytest.raises(ValidationError):
                hourly_energy.SourceHourlyEnergy.model_validate(sample(**patch))
        with pytest.raises(IntegrityError), db.begin_nested():
            row.kwh = "NaN"
            db.flush()
        db.refresh(row)
    with SessionLocal() as db:
        station = db.get(Station, sid)
        with pytest.raises(ValidationError):
            hourly_energy.ingest(
                db,
                "test",
                station.operator_id,
                station,
                [sample(STAMP + timedelta(hours=4)), sample(STAMP + timedelta(hours=5), kwh="-1")],
                is_demo=True,
            )
        db.rollback()
    with SessionLocal() as db:
        assert (
            len(
                list(
                    db.scalars(
                        select(HourlyEnergyRecord).where(HourlyEnergyRecord.station_id == sid)
                    )
                )
            )
            == 1
        )


def test_time_slot_independent_energy_unknown_zero_timezone_and_permissions(accounts, staff):
    names, password, sids = accounts
    profile = None
    with SessionLocal.begin() as db:
        station = db.get(Station, sids[0])
        station.name = "=SUM(1,2)"
        hourly_energy.ingest(
            db,
            "test",
            station.operator_id,
            station,
            [
                sample(),
                sample(STAMP + timedelta(days=1), kwh="0.2"),
                sample(STAMP + timedelta(hours=1), kwh=None),
                sample(STAMP + timedelta(hours=2), kwh="0"),
                sample(meter_scope="equipment", equipment_external_id="A", kwh="99"),
            ],
            is_demo=True,
        )
        hourly_energy.ingest(
            db,
            "test",
            station.operator_id,
            station,
            [sample(STAMP + timedelta(hours=3), kwh="7")],
            is_demo=False,
        )
        data = []
        for i, (minutes, status, equipment) in enumerate(
            [
                (5, "completed", "A"),
                (20, "cancelled", "A"),
                (959, "completed", "B"),
                (960, "completed", "A"),
            ]
        ):
            start = STAMP + timedelta(minutes=minutes)
            data.append(
                {
                    "external_id": f"time-order-{i}",
                    "equipment_external_id": equipment,
                    "status": status,
                    "started_at": start,
                    "ended_at": start + timedelta(hours=1),
                    "source_updated_at": start + timedelta(hours=2),
                    "duration_seconds": 3500,
                    "kwh": "1000",
                    "amount": "1",
                    "currency": "CNY",
                }
            )
        orders.ingest(db, "test", station.operator_id, station, data, is_demo=True)
        uid = db.scalar(select(User.id).where(User.username == names[0]))
    clients = [TestClient(app, client=("time-slots-" + secrets.token_hex(8), 1)) for _ in range(3)]
    params = {
        "station_id": sids[0],
        "source": "test",
        "start_at": STAMP.isoformat(),
        "end_at": (STAMP + timedelta(days=2)).isoformat(),
    }
    try:
        for client, name in zip(clients, [names[0], staff[0][0], staff[0][1]]):
            assert (
                client.post(
                    "/api/v1/auth/login", json={"username": name, "password": password}
                ).status_code
                == 200
            )
        op, reg, association = clients
        assert association.get("/api/v1/orders/time-slots", params=params).status_code == 403
        response = op.get("/api/v1/orders/time-slots", params=params)
        assert response.status_code == 200, response.text
        report = response.json()
        assert len(report["streams"]) == 2 and report["source_completeness_verified"] is False
        stream = next(s for s in report["streams"] if s["is_demo"])
        assert stream["observed_order_starts"] == 4 and len(stream["slots"]) == 24
        points = stream["slots"]
        assert points[8]["observed_order_starts"] == 2 and points[8]["completed_order_starts"] == 1
        assert points[8]["order_start_share_pct"] == "50.00"
        assert points[23]["observed_order_starts"] == 1 and points[0]["observed_order_starts"] == 1
        assert points[8]["kwh"] == "0.300000"  # Not the 2000 kWh of orders starting then.
        assert points[8]["known_meter_slots"] == 2 and points[8]["unreported_meter_slots"] == 0
        assert (
            points[9]["kwh"] is None
            and points[9]["unknown_meter_slots"] == 1
            and points[9]["unreported_meter_slots"] == 1
        )
        assert points[10]["kwh"] == "0.000000" and points[10]["known_meter_slots"] == 1
        assert points[11]["kwh"] is None and points[11]["unreported_meter_slots"] == 2
        other = next(s for s in report["streams"] if not s["is_demo"])
        assert (
            other["slots"][11]["kwh"] == "7.000000"
            and other["slots"][11]["order_start_share_pct"] is None
        )
        equipment = op.get(
            "/api/v1/orders/time-slots",
            params={**params, "meter_scope": "equipment", "equipment_external_id": "A"},
        ).json()
        assert equipment["streams"][0]["slots"][8]["kwh"] == "99.000000"
        assert equipment["streams"][0]["observed_order_starts"] == 3
        assert (
            op.get("/api/v1/orders/time-slots", params={**params, "source": "absent"}).json()[
                "streams"
            ]
            == []
        )
        assert (
            op.get(
                "/api/v1/orders/time-slots", params={**params, "station_id": sids[1]}
            ).status_code
            == 404
        )
        assert (
            reg.get(
                "/api/v1/orders/time-slots", params={**params, "station_id": sids[1]}
            ).status_code
            == 200
        )
        export = op.get("/api/v1/orders/time-slots/export", params=params)
        assert export.status_code == 200
        rows = list(csv.reader(io.StringIO(export.content.decode("utf-8-sig"))))
        assert len(rows) == 49 and rows[1][0] == "'=SUM(1,2)"
        assert any(row[12] == "暂无数据" for row in rows[1:])
        for patch in [
            {"start_at": "2026-11-01T00:00:00"},
            {"start_at": (STAMP + timedelta(minutes=1)).isoformat()},
            {"end_at": STAMP.isoformat()},
            {"end_at": (STAMP + timedelta(days=367)).isoformat()},
            {"meter_scope": "equipment"},
            {"equipment_external_id": "A"},
            {"source": " "},
            {"meter_scope": "invented"},
        ]:
            assert (
                op.get("/api/v1/orders/time-slots", params={**params, **patch}).status_code == 422
            )
        with SessionLocal.begin() as db:
            p = RoleProfile(
                name="time-read-" + secrets.token_hex(8),
                base_role="operator",
                permissions=["stations.read", "orders.read"],
            )
            db.add(p)
            db.flush()
            profile = p.id
            db.get(User, uid).role_profile_id = profile
        assert op.get("/api/v1/orders/time-slots/export", params=params).status_code == 403
        with SessionLocal.begin() as db:
            db.get(User, uid).allowed_districts = []
        assert op.get("/api/v1/orders/time-slots", params=params).status_code == 404
    finally:
        for client in clients:
            client.close()
        with SessionLocal.begin() as db:
            user = db.get(User, uid)
            user.role_profile_id = None
            user.allowed_districts = None
            db.flush()
            if profile:
                db.execute(delete(RoleProfile).where(RoleProfile.id == profile))
