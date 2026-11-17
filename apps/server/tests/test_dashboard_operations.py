import secrets
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, event, select
from test_order_summary import login, record

from app import orders
from app.db import SessionLocal, engine
from app.models import (
    EnergyRecord,
    Equipment,
    Registration,
    RoleProfile,
    Station,
    User,
)

BASE = "/api/v1/dashboard/operations"
QUERY = {"period": "month", "period_date": "2026-11-15"}


@pytest.fixture()
def data(accounts):
    _, _, sids = accounts
    key = secrets.token_hex(8)
    registration = None
    try:
        with SessionLocal.begin() as db:
            own, foreign = [db.get(Station, sid) for sid in sids]
            own.status = "operating"
            own.district = "scope-" + key
            db.add(Equipment(station_id=own.id, external_id="D", name="Fixture", power_kw=1))
            proof = Registration(
                username="proof-" + key,
                password_hash="unused-test-proof",
                company_name="proof-" + key,
                credit_code=key[:18],
                contacts=[],
                status="approved",
                operator_id=own.operator_id,
            )
            db.add(proof)
            db.flush()
            registration = proof.id
            for station, day, value, external in [
                (own, date(2026, 11, 1), "0.1", "one"),
                (own, date(2026, 11, 1), "0.2", "two"),
                (own, date(2026, 11, 2), "0", "zero"),
                (own, date(2025, 12, 1), "1", "prior"),
                (foreign, date(2026, 11, 1), "900", "foreign"),
            ]:
                db.add(
                    EnergyRecord(
                        station_id=station.id,
                        source="dashboard-test",
                        external_id=key + external,
                        day=day,
                        kwh=Decimal(value),
                        eligible=True,
                    )
                )
            orders.ingest(
                db,
                "test",
                own.operator_id,
                own,
                [
                    record("known"),
                    record("missing", amount=None, kwh=None, duration_seconds=None, currency=None),
                ],
                is_demo=True,
            )
        yield sids
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(EnergyRecord).where(EnergyRecord.station_id.in_(sids)))
            db.execute(delete(Equipment).where(Equipment.station_id.in_(sids)))
            db.execute(delete(Registration).where(Registration.id == registration))


def test_stock_period_metrics_trailing_months_and_company_proof_are_scoped(data, accounts, staff):
    with (
        login(accounts[0][0], accounts[1]) as own,
        login(accounts[0][1], accounts[1]) as other,
        login(staff[0][1], accounts[1]) as association,
    ):
        result = own.get(BASE, params=QUERY)
        assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
        value = result.json()
        assert (
            value["current_stations"]
            == value["current_equipment"]
            == value["operators_with_stations"]
            == value["approved_company_records"]
            == 1
        )
        assert Decimal(value["current_equipment_power_kw"]) == 1
        assert value["statuses"] == [{"key": "operating", "count": 1, "share_pct": "100.00"}]
        assert value["approvals"]["approval_rate_pct"] is None
        assert (
            value["orders"][0]["completed_orders"] == 2
            and value["orders"][0]["kwh"]["missing_orders"] == 1
        )
        energy = value["energy"][0]
        assert (
            Decimal(energy["kwh"]) == Decimal("0.3")
            and energy["observed_station_days"] == 2
            and energy["duplicate_station_days"] == 1
        )
        trend = value["energy_trend"][0]["months"]
        assert len(trend) == 12 and trend[0]["month"] == "2025-12-01"
        assert (
            Decimal(trend[0]["kwh"]) == 1 and trend[1]["kwh"] is None and trend[1]["records"] == 0
        )
        zero = own.get(BASE, params={"period": "day", "period_date": "2026-11-02"}).json()
        assert Decimal(zero["energy"][0]["kwh"]) == 0
        assert other.get(BASE, params=QUERY).json()["current_equipment"] == 0
        assert other.get(BASE, params=QUERY).json()["energy"][0]["kwh"] != energy["kwh"]
        assert association.get(BASE, params=QUERY).status_code == 403
        leap = own.get(BASE, params={"period": "year", "period_date": "2024-02-29"}).json()
        assert (
            leap["start_date"] == "2024-01-01"
            and leap["end_date"] == "2024-12-31"
            and leap["energy"] == []
        )
        assert own.get(BASE, params={"period_date": "9999-01-01"}).status_code == 422


def test_missing_capabilities_hide_observations_and_approval_proofs(data, accounts):
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.username == accounts[0][0]))
        uid = user.id
        profile = RoleProfile(
            name="dashboard-stock-" + secrets.token_hex(6),
            base_role="operator",
            permissions=["stations.read"],
            version=1,
        )
        db.add(profile)
        db.flush()
        pid = profile.id
        user.role_profile_id = pid
    try:
        with login(accounts[0][0], accounts[1]) as own:
            value = own.get(BASE, params=QUERY).json()
            assert value["current_stations"] == 1
            assert all(
                value[key] is None
                for key in [
                    "approved_company_records",
                    "approvals",
                    "orders",
                    "energy",
                    "energy_trend",
                ]
            )
    finally:
        with SessionLocal.begin() as db:
            db.get(User, uid).role_profile_id = None
            db.execute(delete(RoleProfile).where(RoleProfile.id == pid))


def test_operations_reads_one_snapshot_across_stock_and_independent_sources(data, accounts):
    fired = False

    def after_count(connection, cursor, statement, params, context, many):
        nonlocal fired
        if fired or "count(*)" not in statement or "FROM (SELECT stations.id" not in statement:
            return
        fired = True
        with SessionLocal.begin() as db:
            db.get(Station, data[0]).status = "decommissioned"
            db.add(
                EnergyRecord(
                    station_id=data[0],
                    source="dashboard-test",
                    external_id="concurrent-" + secrets.token_hex(6),
                    day=date(2026, 11, 1),
                    kwh=Decimal(10),
                    eligible=True,
                )
            )

    with login(accounts[0][0], accounts[1]) as own:
        event.listen(engine, "after_cursor_execute", after_count)
        try:
            first = own.get(BASE, params=QUERY).json()
            assert fired and first["statuses"][0]["key"] == "operating"
            assert Decimal(first["energy"][0]["kwh"]) == Decimal("0.3")
        finally:
            event.remove(engine, "after_cursor_execute", after_count)
        second = own.get(BASE, params=QUERY).json()
        assert second["statuses"][0]["key"] == "decommissioned"
        assert Decimal(second["energy"][0]["kwh"]) == Decimal("10.3")


def test_regional_restrictions_apply_to_stock_proofs_orders_and_energy(data, accounts, staff):
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.username == staff[0][0]))
        uid = user.id
        user.allowed_districts = [db.get(Station, data[0]).district]
    try:
        with login(staff[0][0], accounts[1]) as reg:
            value = reg.get(BASE, params=QUERY).json()
            assert value["current_stations"] == value["approved_company_records"] == 1
            assert value["orders"][0]["completed_orders"] == 2
            assert Decimal(value["energy"][0]["kwh"]) == Decimal("0.3")
            with SessionLocal.begin() as db:
                db.get(User, uid).allowed_districts = []
            empty = reg.get(BASE, params=QUERY).json()
            assert (
                empty["current_stations"]
                == empty["current_equipment"]
                == empty["approved_company_records"]
                == 0
            )
            assert (
                empty["statuses"]
                == empty["districts"]
                == empty["orders"]
                == empty["energy"]
                == empty["energy_trend"]
                == []
            )
    finally:
        with SessionLocal.begin() as db:
            db.get(User, uid).allowed_districts = None


def test_two_concurrent_snapshot_reads_finish_with_exactly_two_pool_connections(
    data, accounts, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import db as database
    from app import reporting

    pool = create_engine(engine.url, pool_size=2, max_overflow=0, pool_timeout=0.5)
    barrier = Barrier(2)
    arrivals = []

    def rendezvous(connection, cursor, statement, parameters, context, many):
        if statement.startswith("SELECT login_sessions."):
            arrivals.append(True)
            barrier.wait(timeout=5)

    try:
        with login(accounts[0][0], accounts[1]) as own, login(accounts[0][1], accounts[1]) as other:
            event.listen(pool, "after_cursor_execute", rendezvous)
            with monkeypatch.context() as patch:
                patch.setattr(database, "SessionLocal", sessionmaker(pool, expire_on_commit=False))
                patch.setattr(reporting, "engine", pool)
                with ThreadPoolExecutor(max_workers=2) as workers:
                    futures = [
                        workers.submit(client.get, BASE, params=QUERY) for client in [own, other]
                    ]
                    results = [future.result(timeout=10) for future in futures]
                assert len(arrivals) == 2 and all(result.status_code == 200 for result in results)
                assert (
                    results[0].json()["current_equipment"] == 1
                    and results[1].json()["current_equipment"] == 0
                )
                assert pool.pool.checkedout() == 0
    finally:
        pool.dispose()
