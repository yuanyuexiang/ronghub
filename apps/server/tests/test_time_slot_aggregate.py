import csv
import io
import secrets
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import delete, event, select

from app import hourly_energy, orders
from app.db import SessionLocal, engine
from app.main import app
from app.models import ChargingOrder, HourlyEnergyRecord, RoleProfile, Station, User

STAMP = datetime(2026, 11, 1, tzinfo=UTC)


def hour(value, **patch):
    return {
        "meter_scope": "station",
        "started_at": STAMP,
        "ended_at": STAMP + timedelta(hours=1),
        "kwh": value,
        "source_updated_at": STAMP + timedelta(hours=2),
        **patch,
    }


def order(key):
    return {
        "external_id": key,
        "status": "completed",
        "started_at": STAMP,
        "ended_at": STAMP + timedelta(hours=1),
        "source_updated_at": STAMP + timedelta(hours=2),
        "kwh": "1000",
    }


def client(name, password):
    c = TestClient(app, client=("multi-slots-" + secrets.token_hex(8), 1))
    assert (
        c.post("/api/v1/auth/login", json={"username": name, "password": password}).status_code
        == 200
    )
    return c


def extra_station(db, oid, source, suffix):
    station = Station(
        operator_id=oid,
        source=source,
        external_id="extra-" + suffix,
        name="multi-" + suffix,
        district="D2",
        address="fixture",
        connectors=1,
        power_kw=10,
    )
    db.add(station)
    db.flush()
    return station


def clean_extra(ids):
    with SessionLocal.begin() as db:
        db.execute(delete(HourlyEnergyRecord).where(HourlyEnergyRecord.station_id.in_(ids)))
        db.execute(delete(ChargingOrder).where(ChargingOrder.station_id.in_(ids)))
        db.execute(delete(Station).where(Station.id.in_(ids)))


def test_multi_station_sum_scope_meter_layers_and_empty_observations(accounts, staff):
    names, password, sids = accounts
    source = "multi-" + secrets.token_hex(8)
    extras = []
    profile = None
    with SessionLocal.begin() as db:
        first, foreign = db.get(Station, sids[0]), db.get(Station, sids[1])
        first.source = foreign.source = source
        first.district = "D1"
        first.name = "literal%_station"
        other = extra_station(db, first.operator_id, source, secrets.token_hex(8))
        extras.append(other.id)
        empty = extra_station(db, first.operator_id, source, secrets.token_hex(8))
        extras.append(empty.id)
        for station, value in [(first, "0.1"), (other, "0.2"), (foreign, "99")]:
            hourly_energy.ingest(
                db, source, station.operator_id, station, [hour(value)], is_demo=True
            )
            orders.ingest(
                db,
                source,
                station.operator_id,
                station,
                [order("order-" + str(station.id))],
                is_demo=True,
            )
        hourly_energy.ingest(
            db,
            source,
            first.operator_id,
            first,
            [hour("777", meter_scope="equipment", equipment_external_id="A")],
            is_demo=True,
        )
        hourly_energy.ingest(
            db,
            source,
            first.operator_id,
            first,
            [hour("7", started_at=STAMP + timedelta(hours=1), ended_at=STAMP + timedelta(hours=2))],
            is_demo=False,
        )
        uid = db.scalar(select(User.id).where(User.username == names[0]))
    params = {
        "source": source,
        "start_at": STAMP.isoformat(),
        "end_at": (STAMP + timedelta(days=1)).isoformat(),
    }
    clients = [client(name, password) for name in [names[0], staff[0][0], staff[0][1]]]
    try:
        op, reg, association = clients
        assert (
            association.get("/api/v1/orders/time-slots/aggregate", params=params).status_code == 403
        )
        assert association.get("/api/v1/orders/time-slots/aggregate/sources").status_code == 403
        response = op.get("/api/v1/orders/time-slots/aggregate", params=params)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["station_count"] == 3
        stream = next(s for s in result["streams"] if s["is_demo"])
        assert (
            stream["stations_with_order_observations"] == stream["stations_with_meter_records"] == 2
        )
        assert stream["observed_order_starts"] == 2
        assert stream["slots"][8]["kwh"] == "0.300000"
        assert (
            stream["slots"][8]["selected_slots"] == 3
            and stream["slots"][8]["unreported_meter_slots"] == 1
        )
        assert (
            stream["slots"][9]["kwh"] is None and stream["slots"][9]["unreported_meter_slots"] == 3
        )
        assert (
            next(s for s in result["streams"] if not s["is_demo"])["slots"][9]["kwh"] == "7.000000"
        )
        broader = reg.get("/api/v1/orders/time-slots/aggregate", params=params).json()
        assert broader["station_count"] == 4
        assert next(s for s in broader["streams"] if s["is_demo"])["slots"][8]["kwh"] == "99.300000"
        narrowed = op.get(
            "/api/v1/orders/time-slots/aggregate", params={**params, "district": "D1"}
        ).json()
        assert (
            narrowed["station_count"] == 1
            and narrowed["streams"][0]["slots"][9]["kwh"] == "7.000000"
        )
        assert (
            op.get("/api/v1/orders/time-slots/aggregate", params={**params, "q": "%_"}).json()[
                "station_count"
            ]
            == 1
        )
        assert (
            op.get(
                "/api/v1/orders/time-slots/aggregate", params={**params, "q": empty.name}
            ).json()["streams"]
            == []
        )
        outside = op.get(
            "/api/v1/orders/time-slots/aggregate",
            params={**params, "operator_id": foreign.operator_id},
        ).json()
        assert outside["station_count"] == 0 and outside["streams"] == []
        options = op.get("/api/v1/orders/time-slots/aggregate/sources", params={"q": source}).json()
        assert options["total"] == 1 and options["items"][0]["station_count"] == 3
        exported = op.get(
            "/api/v1/orders/time-slots/aggregate/export", params={**params, "q": "%_"}
        )
        assert exported.status_code == 200
        rows = list(csv.reader(io.StringIO(exported.content.decode("utf-8-sig"))))
        assert len(rows) == 49 and all(row[1] == "1" for row in rows[1:])
        for patch in [
            {"meter_scope": "equipment"},
            {"equipment_external_id": "A"},
            {"start_at": "2026-11-01T00:00:00"},
            {"end_at": params["start_at"]},
            {"operator_id": -1},
            {"source": " "},
        ]:
            assert (
                op.get(
                    "/api/v1/orders/time-slots/aggregate", params={**params, **patch}
                ).status_code
                == 422
            )
        with SessionLocal.begin() as db:
            p = RoleProfile(
                name="multi-read-" + secrets.token_hex(8),
                base_role="operator",
                permissions=["stations.read", "orders.read"],
            )
            db.add(p)
            db.flush()
            profile = p.id
            db.get(User, uid).role_profile_id = profile
        assert (
            op.get("/api/v1/orders/time-slots/aggregate/export", params=params).status_code == 403
        )
        with SessionLocal.begin() as db:
            db.get(User, uid).allowed_districts = []
        assert (
            op.get("/api/v1/orders/time-slots/aggregate", params=params).json()["station_count"]
            == 0
        )
        assert op.get("/api/v1/orders/time-slots/aggregate/sources").json()["total"] == 0
    finally:
        for c in clients:
            c.close()
        with SessionLocal.begin() as db:
            u = db.get(User, uid)
            u.role_profile_id = None
            u.allowed_districts = None
            db.flush()
            if profile:
                db.execute(delete(RoleProfile).where(RoleProfile.id == profile))
        clean_extra(extras)


def test_multi_station_inventory_uses_one_real_pg_snapshot(accounts):
    names, password, sids = accounts
    source = "snapshot-" + secrets.token_hex(8)
    extra_ids = []
    with SessionLocal.begin() as db:
        first = db.get(Station, sids[0])
        first.source = source
        second = extra_station(db, first.operator_id, source, secrets.token_hex(8))
        extra_ids.append(second.id)
        second_id = second.id
        for station, value in [(first, "0.1"), (second, "0.2")]:
            hourly_energy.ingest(
                db, source, station.operator_id, station, [hour(value)], is_demo=True
            )
    injected = []

    def move_after_count(connection, cursor, statement, parameters, context, executemany):
        if (
            not injected
            and statement.startswith("SELECT count(*)")
            and "stations.source" in statement
            and "operators" in statement
        ):
            injected.append(True)
            with SessionLocal.begin() as writer:
                writer.get(Station, second_id).source = source + "-moved"

    params = {
        "source": source,
        "start_at": STAMP.isoformat(),
        "end_at": (STAMP + timedelta(days=1)).isoformat(),
    }
    try:
        with client(names[0], password) as c:
            event.listen(engine, "after_cursor_execute", move_after_count)
            try:
                result = c.get("/api/v1/orders/time-slots/aggregate", params=params)
            finally:
                event.remove(engine, "after_cursor_execute", move_after_count)
            assert injected and result.status_code == 200
            data = result.json()
            assert (
                data["station_count"] == 2 and data["streams"][0]["slots"][8]["kwh"] == "0.300000"
            )
            newer = c.get("/api/v1/orders/time-slots/aggregate", params=params).json()
            assert (
                newer["station_count"] == 1 and newer["streams"][0]["slots"][8]["kwh"] == "0.100000"
            )
    finally:
        clean_extra(extra_ids)


def test_meter_source_history_pagination_is_scoped_and_literal(accounts):
    names, password, sids = accounts
    prefix = "sources-" + secrets.token_hex(8)
    ids = []
    with SessionLocal.begin() as db:
        owner = db.get(Station, sids[0]).operator_id
        for i in range(122):
            s = extra_station(db, owner, prefix + f"-{i:03d}", secrets.token_hex(8))
            ids.append(s.id)
        s = extra_station(db, owner, prefix + "-%_", secrets.token_hex(8))
        ids.append(s.id)
    try:
        with client(names[0], password) as c:
            page = c.get(
                "/api/v1/orders/time-slots/aggregate/sources", params={"q": prefix, "page": 7}
            ).json()
            assert page["total"] == 123 and len(page["items"]) == 3
            literal = c.get(
                "/api/v1/orders/time-slots/aggregate/sources", params={"q": "%_"}
            ).json()
            assert literal["total"] == 1 and literal["items"][0]["source"] == prefix + "-%_"
            assert (
                c.get(
                    "/api/v1/orders/time-slots/aggregate/sources", params={"page_size": 101}
                ).status_code
                == 422
            )
    finally:
        clean_extra(ids)
