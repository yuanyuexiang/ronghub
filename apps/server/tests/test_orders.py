import csv
import io
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app import orders
from app.db import SessionLocal
from app.main import app
from app.models import Audit, ChargingOrder, RoleProfile, Station, User

STAMP = datetime(2026, 11, 1, tzinfo=UTC)


def record(external="order", **patch):
    return {
        "external_id": external,
        "status": "completed",
        "started_at": STAMP,
        "ended_at": STAMP + timedelta(hours=1),
        "source_updated_at": STAMP + timedelta(hours=2),
        "equipment_external_id": "equipment-1",
        "connector_external_id": "connector-1",
        "duration_seconds": 3300,
        "kwh": "42.005000",
        "amount": "50.41",
        "currency": "CNY",
        **patch,
    }


def test_order_source_revisions_validation_and_atomicity(accounts):
    _, _, sids = accounts
    with SessionLocal.begin() as db:
        station = db.get(Station, sids[0])
        assert (
            orders.ingest(db, "test", station.operator_id, station, [record()], is_demo=True)[
                "changed"
            ]
            == 1
        )
        assert (
            orders.ingest(db, "test", station.operator_id, station, [record()], is_demo=True)[
                "changed"
            ]
            == 0
        )
    with SessionLocal.begin() as db:
        station = db.get(Station, sids[0])
        assert (
            orders.ingest(
                db,
                "test",
                station.operator_id,
                station,
                [record(source_updated_at=STAMP)],
                is_demo=True,
            )["ignored_stale"]
            == 1
        )
        order = db.scalar(select(ChargingOrder).where(ChargingOrder.station_id == station.id))
        assert order.version == 1 and str(order.kwh) == "42.005000"
        assert order.duration_seconds == 3300  # Not the 3600-second wall-clock interval.
    with SessionLocal() as db:
        station = db.get(Station, sids[0])
        with pytest.raises(ValueError, match="conflicting"):
            orders.ingest(
                db, "test", station.operator_id, station, [record(amount="99.00")], is_demo=True
            )
        db.rollback()
    with SessionLocal.begin() as db:
        station = db.get(Station, sids[0])
        assert (
            orders.ingest(
                db,
                "test",
                station.operator_id,
                station,
                [record(amount="51.00", source_updated_at=STAMP + timedelta(hours=3))],
                is_demo=True,
            )["changed"]
            == 1
        )
        order = db.scalar(select(ChargingOrder).where(ChargingOrder.station_id == station.id))
        assert order.version == 2 and str(order.amount) == "51.00"
        with pytest.raises(ValueError, match="ownership"):
            orders.ingest(
                db, "test", station.operator_id + 100000, station, [record()], is_demo=True
            )
        for patch in [
            {"started_at": datetime(2026, 11, 1)},
            {"kwh": "NaN"},
            {"amount": "-1"},
            {"currency": None},
            {"status": "charging"},
            {"duration_seconds": -1},
            {"duration_seconds": True},
            {"vehicle_plate": "private-value"},
            {"ended_at": STAMP - timedelta(seconds=1)},
        ]:
            with pytest.raises((ValidationError, ValueError)):
                orders.SourceOrder.model_validate(record(**patch))
        with pytest.raises(IntegrityError), db.begin_nested():
            order.kwh = "NaN"
            db.flush()
        db.refresh(order)
        assert str(order.kwh) == "42.005000"
    # A later invalid item cannot leave a partially committed batch.
    with SessionLocal() as db:
        station = db.get(Station, sids[0])
        with pytest.raises(ValidationError):
            orders.ingest(
                db,
                "test",
                station.operator_id,
                station,
                [record("partial"), record("bad", amount="-1")],
                is_demo=True,
            )
        db.rollback()
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(ChargingOrder)
                .where(ChargingOrder.station_id == sids[0])
            )
            == 1
        )


def test_order_pages_scope_unknown_values_and_safe_export(accounts, staff):
    names, password, sids = accounts
    profile = None
    with SessionLocal.begin() as db:
        station = db.get(Station, sids[0])
        rows = [
            record(
                f"history-{i}",
                started_at=STAMP + timedelta(minutes=i),
                ended_at=STAMP + timedelta(minutes=i + 60),
            )
            for i in range(122)
        ]
        rows[0] = record("=SUM(1,2)", duration_seconds=None, kwh=None, amount=None, currency=None)
        rows[1] = record(
            "literal%_",
            started_at=STAMP + timedelta(minutes=1),
            ended_at=STAMP + timedelta(minutes=61),
        )
        orders.ingest(db, "test", station.operator_id, station, rows, is_demo=True)
        other = db.get(Station, sids[1])
        orders.ingest(db, "test", other.operator_id, other, [record("foreign")], is_demo=True)
        owner_id = db.scalar(select(User.id).where(User.username == names[0]))
    clients = [TestClient(app, client=("orders-" + secrets.token_hex(8), 1)) for _ in range(3)]
    try:
        for c, username in zip(clients, [names[0], staff[0][0], staff[0][1]]):
            assert (
                c.post(
                    "/api/v1/auth/login", json={"username": username, "password": password}
                ).status_code
                == 200
            )
        op, reg, association = clients
        assert association.get("/api/v1/orders").status_code == 403
        assert association.get("/api/v1/orders/export").status_code == 403
        page = op.get("/api/v1/orders", params={"page": 7}).json()
        assert page["total"] == 122 and len(page["items"]) == 2
        assert page["source_completeness_verified"] is False
        assert op.get("/api/v1/orders", params={"station_id": sids[1]}).json()["total"] == 0
        assert reg.get("/api/v1/orders", params={"station_id": sids[1]}).json()["total"] == 1
        literal = op.get("/api/v1/orders", params={"q": "%_"}).json()
        assert literal["total"] == 1 and literal["items"][0]["external_id"] == "literal%_"
        unknown = op.get("/api/v1/orders", params={"q": "=SUM"}).json()["items"][0]
        assert (
            unknown["amount"] is None
            and unknown["kwh"] is None
            and unknown["duration_seconds"] is None
        )
        assert (
            op.get(
                "/api/v1/orders",
                params={
                    "equipment_external_id": "equipment-1",
                    "start_at": STAMP.isoformat(),
                    "end_at": (STAMP + timedelta(minutes=1)).isoformat(),
                },
            ).json()["total"]
            == 1
        )
        for params in [
            {"status": "invented"},
            {"page": 0},
            {"page_size": 101},
            {"station_id": 9999999999999999},
            {"start_at": "2026-11-01T00:00:00"},
            {"start_at": STAMP.isoformat(), "end_at": STAMP.isoformat()},
            {"start_at": STAMP.isoformat(), "end_at": (STAMP + timedelta(days=367)).isoformat()},
        ]:
            assert op.get("/api/v1/orders", params=params).status_code == 422
        exported = op.get("/api/v1/orders/export")
        assert exported.status_code == 200
        csv_rows = list(csv.reader(io.StringIO(exported.content.decode("utf-8-sig"))))
        assert len(csv_rows) == 123 and not any("foreign" in row for row in csv_rows)
        assert any(row[2] == "'=SUM(1,2)" and row[11] == "暂无数据" for row in csv_rows[1:])
        with SessionLocal.begin() as db:
            assert db.scalar(
                select(Audit.id).where(Audit.user_id == owner_id, Audit.action == "orders.export")
            )
            p = RoleProfile(
                name="order-readonly-" + secrets.token_hex(8),
                base_role="operator",
                permissions=["stations.read", "orders.read"],
            )
            db.add(p)
            db.flush()
            profile = p.id
            db.get(User, owner_id).role_profile_id = profile
        assert op.get("/api/v1/orders").status_code == 200
        assert op.get("/api/v1/orders/export").status_code == 403
        summary_window = {
            "start_at": STAMP.isoformat(),
            "end_at": (STAMP + timedelta(days=1)).isoformat(),
        }
        assert op.get("/api/v1/orders/summary", params=summary_window).status_code == 200
        assert op.get("/api/v1/orders/summary/export", params=summary_window).status_code == 403
        with SessionLocal.begin() as db:
            db.get(User, owner_id).allowed_districts = []
        assert op.get("/api/v1/orders").json()["total"] == 0
    finally:
        for c in clients:
            c.close()
        with SessionLocal.begin() as db:
            u = db.get(User, owner_id)
            u.role_profile_id = None
            u.allowed_districts = None
            db.flush()
            if profile:
                db.execute(delete(RoleProfile).where(RoleProfile.id == profile))


def test_concurrent_identical_order_import_is_idempotent(accounts):
    sid = accounts[2][0]

    def run():
        with SessionLocal.begin() as db:
            station = db.get(Station, sid)
            return orders.ingest(
                db, "test", station.operator_id, station, [record("concurrent")], is_demo=True
            )["changed"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: run(), range(2))) == [0, 1]
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(ChargingOrder)
                .where(ChargingOrder.station_id == sid)
            )
            == 1
        )


def test_order_export_limit_rejects_truncation_and_small_query_succeeds(accounts):
    from sqlalchemy import insert

    names, password, sids = accounts
    with SessionLocal.begin() as db:
        station = db.get(Station, sids[0])
        uid = db.scalar(select(User.id).where(User.username == names[0]))
        db.execute(
            insert(ChargingOrder),
            [
                {
                    "station_id": station.id,
                    "operator_id": station.operator_id,
                    "source": "test",
                    "external_id": f"export-limit-{i}",
                    "equipment_external_id": f"export-pile-{i}",
                    "status": "charging",
                    "started_at": STAMP,
                    "source_updated_at": STAMP,
                    "is_demo": True,
                }
                for i in range(10001)
            ],
        )
    with TestClient(app, client=("orders-limit-" + secrets.token_hex(8), 1)) as client:
        assert (
            client.post(
                "/api/v1/auth/login", json={"username": names[0], "password": password}
            ).status_code
            == 200
        )
        response = client.get("/api/v1/orders/export")
        assert response.status_code == 422
        with SessionLocal() as db:
            assert (
                db.scalar(
                    select(Audit.id).where(Audit.user_id == uid, Audit.action == "orders.export")
                )
                is None
            )
        summary_window = {
            "start_at": STAMP.isoformat(),
            "end_at": (STAMP + timedelta(days=1)).isoformat(),
            "dimension": "equipment",
        }
        assert client.get("/api/v1/orders/summary/export", params=summary_window).status_code == 422
        assert (
            client.get(
                "/api/v1/orders/summary/export",
                params={**summary_window, "q": "export-limit-10000"},
            ).status_code
            == 200
        )
        smaller = client.get("/api/v1/orders/export", params={"q": "export-limit-10000"})
        assert smaller.status_code == 200
        assert len(list(csv.reader(io.StringIO(smaller.content.decode("utf-8-sig"))))) == 2
