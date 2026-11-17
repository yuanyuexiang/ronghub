import secrets
from contextlib import ExitStack
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, select, text
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal, engine
from app.main import app
from app.models import Connector, Equipment, Station, User
from app.sync import ingest


def payload(s, **extra):
    return {
        "external_id": s.external_id,
        "name": s.name,
        "district": s.district,
        "address": s.address,
        "power_kw": "120",
        "equipment": [
            {
                "external_id": "source-code%_",
                "name": "原始设备",
                "power_kw": "60",
                "equipment_type": "v2g",
                "model_name": "DEMO-MODEL",
                "manufactured_on": "2024-02-29",
                "source_unique_code": "REPORTED-ONLY",
                "connectors": [
                    {
                        "external_id": "terminal-1",
                        "status": "reserved",
                        "name": "预约终端",
                        "interface_type": "来源接口说明",
                    }
                ],
            }
        ],
        **extra,
    }


@pytest.fixture()
def devices(accounts):
    yield
    with SessionLocal.begin() as db:
        ids = select(Equipment.id).where(Equipment.station_id.in_(accounts[2]))
        db.execute(delete(Connector).where(Connector.equipment_id.in_(ids)))
        db.execute(delete(Equipment).where(Equipment.station_id.in_(accounts[2])))


def test_source_profiles_preserve_natural_ids_and_partial_values_and_clear(accounts, devices):
    sid = accounts[2][0]
    with SessionLocal.begin() as db:
        s = db.get(Station, sid)
        s.status = "operating"
        body = payload(s, usage_type="private_shared", construction_site="fuel_station")
        # Descriptive profiles may be trimmed; preexisting natural identity is not rewritten.
        body["equipment"][0]["connectors"][0]["external_id"] = "  source terminal  "
        ingest(db, s.source, s.operator_id, [body, body])
    with SessionLocal.begin() as db:
        e = db.scalar(select(Equipment).where(Equipment.station_id == sid))
        c = db.scalar(select(Connector).where(Connector.equipment_id == e.id))
        assert c.external_id == "  source terminal  "
        assert (e.equipment_type, e.model_name, e.source_unique_code, e.manufactured_on) == (
            "v2g",
            "DEMO-MODEL",
            "REPORTED-ONLY",
            date(2024, 2, 29),
        )
        assert c.name == "预约终端" and c.interface_type == "来源接口说明"
        original_ids = (e.id, c.id)
        s = db.get(Station, sid)
        body["equipment"][0] = {
            "external_id": e.external_id,
            "name": "接口改名",
            "power_kw": "60",
            "connectors": [{"external_id": c.external_id, "status": "charging"}],
        }
        ingest(db, s.source, s.operator_id, [body])
    with SessionLocal.begin() as db:
        e, c = db.get(Equipment, original_ids[0]), db.get(Connector, original_ids[1])
        assert e.manufactured_on == date(2024, 2, 29) and e.source_unique_code == "REPORTED-ONLY"
        assert c.name == "预约终端" and c.status == "charging"
        body["equipment"][0].update(
            equipment_type=None, model_name=None, source_unique_code=None, manufactured_on=None
        )
        body["equipment"][0]["connectors"][0].update(name=None, interface_type=None)
        s = db.get(Station, sid)
        ingest(db, s.source, s.operator_id, [body])
    with SessionLocal() as db:
        e, c = db.get(Equipment, original_ids[0]), db.get(Connector, original_ids[1])
        assert (
            e.equipment_type is None
            and e.manufactured_on is None
            and e.model_name is None
            and e.source_unique_code is None
        )
        assert c.name is None and c.interface_type is None
        assert db.get(Station, sid).status == "operating"


def login(stack, name, password):
    client = stack.enter_context(TestClient(app, client=("device-" + secrets.token_hex(5), 1)))
    assert (
        client.post("/api/v1/auth/login", json={"username": name, "password": password}).status_code
        == 200
    )
    return client


def test_bounded_devices_and_terminals_filters_literal_search_scope_and_empty_equipment(
    accounts, staff, devices
):
    names, password, sids = accounts
    with SessionLocal.begin() as db:
        for index in range(23):
            e = Equipment(
                station_id=sids[0],
                external_id=f"device-{index}" if index else "literal%_code",
                name=f"设备{index}",
                power_kw=60,
                equipment_type="dc" if index < 2 else None,
            )
            db.add(e)
            db.flush()
            for n in range(23 if index == 0 else (1 if index == 1 else 0)):
                db.add(
                    Connector(
                        equipment_id=e.id,
                        external_id=f"terminal-{n}",
                        name=f"终端{n}",
                        interface_type="直流接口枪头" if n == 0 else None,
                        status="reserved" if n % 2 else "charging",
                    )
                )
            if index == 0:
                eid = e.id
        other = Equipment(
            station_id=sids[1], external_id="private-other", name="另企设备", power_kw=120
        )
        db.add(other)
        db.flush()
        other_eid = other.id
    with ExitStack() as stack:
        owner = login(stack, names[0], password)
        other = login(stack, names[1], password)
        reg = login(stack, staff[0][0], password)
        assoc = login(stack, staff[0][1], password)
        base = f"/api/v1/stations/{sids[0]}/equipment"
        page = owner.get(base + "/query?page=2&page_size=10").json()
        assert (
            page["total"],
            page["page"],
            len(page["items"]),
            page["station_equipment_count"],
            page["station_connector_count"],
        ) == (23, 2, 10, 23, 24)
        assert page["matching_connector_count"] == 24
        result = owner.get(
            base + "/query",
            params={"q": "%_", "equipment_type": "dc", "connector_status": "reserved"},
        ).json()
        assert result["total"] == 1 and result["items"][0]["id"] == eid
        assert (
            result["items"][0]["connector_count"] == 23
            and result["items"][0]["matching_connectors"]
            == result["matching_connector_count"]
            == 11
        )
        assert owner.get(base + "/query?equipment_type=unknown").json()["total"] == 21
        assert owner.get(base + "/query?connector_status=nonexistent").json()["total"] == 0
        assert owner.get(base + "/query?equipment_type=guessed").status_code == 422
        assert owner.get(base + "/query?page_size=101").status_code == 422
        assert owner.get(base + "/query?page=2147483648").status_code == 422
        terminal = owner.get(f"{base}/{eid}/connectors", params={"page": 2, "page_size": 10}).json()
        assert (
            terminal["total"] == terminal["inventory_total"] == 23 and len(terminal["items"]) == 10
        )
        assert (
            owner.get(f"{base}/{eid}/connectors", params={"q": "terminal-22"}).json()["total"] == 1
        )
        assert (
            owner.get(f"{base}/{eid}/connectors", params={"connector_status": "reserved"}).json()[
                "total"
            ]
            == 11
        )
        for path in [base + "/query", f"{base}/{eid}/connectors"]:
            assert other.get(path).status_code == 404
            assert reg.get(path).status_code == assoc.get(path).status_code == 200
        assert owner.get(f"{base}/{other_eid}/connectors").status_code == 404
        with SessionLocal.begin() as db:
            db.scalar(select(User).where(User.username == staff[0][0])).allowed_districts = []
        assert reg.get(base + "/query").status_code == 404


def test_device_page_keeps_count_items_profiles_and_connector_counts_in_one_snapshot(
    accounts, devices
):
    names, password, sids = accounts
    with SessionLocal.begin() as db:
        s = db.get(Station, sids[0])
        ingest(db, s.source, s.operator_id, [payload(s)])
        eid = db.scalar(select(Equipment.id).where(Equipment.station_id == s.id))
    fired = False

    def commit_after_count(connection, cursor, statement, parameters, context, many):
        nonlocal fired
        if fired or "count(*)" not in statement.lower() or "equipment" not in statement.lower():
            return
        fired = True
        with SessionLocal.begin() as db:
            e = db.get(Equipment, eid)
            e.model_name = "Concurrent new model"
            db.add(Connector(equipment_id=eid, external_id="concurrent", status="reserved"))
            db.add(
                Equipment(
                    station_id=sids[0], external_id="new-concurrent", name="new", power_kw=120
                )
            )

    with ExitStack() as stack:
        client = login(stack, names[0], password)
        event.listen(engine, "after_cursor_execute", commit_after_count)
        try:
            first = client.get(f"/api/v1/stations/{sids[0]}/equipment/query").json()
        finally:
            event.remove(engine, "after_cursor_execute", commit_after_count)
        assert fired and first["total"] == first["station_equipment_count"] == 1
        assert first["station_connector_count"] == first["items"][0]["connector_count"] == 1
        assert first["items"][0]["model_name"] == "DEMO-MODEL"
        second = client.get(f"/api/v1/stations/{sids[0]}/equipment/query").json()
        assert second["total"] == second["station_connector_count"] == 2
        assert second["items"][0]["model_name"] == "Concurrent new model"


@pytest.mark.parametrize(
    "where,bad",
    [
        ("equipment", {"equipment_type": 1}),
        ("equipment", {"equipment_type": "unknown"}),
        ("equipment", {"model_name": "x" * 101}),
        ("equipment", {"source_unique_code": "bad\nvalue"}),
        ("equipment", {"manufactured_on": "2023-02-29"}),
        ("equipment", {"manufactured_on": "20240229"}),
        ("equipment", {"manufactured_on": "2024-02-29T00:00:00Z"}),
        ("connector", {"name": "x" * 151}),
        ("connector", {"interface_type": True}),
        ("connector", {"external_id": " "}),
        ("connector", {"status": None}),
    ],
)
def test_invalid_source_profile_rolls_back_complete_sync_batch(accounts, devices, where, bad):
    with pytest.raises(ValueError), SessionLocal.begin() as db:
        s = db.get(Station, accounts[2][0])
        good = payload(s)
        broken = payload(s, name="must not persist")
        target = (
            broken["equipment"][0]
            if where == "equipment"
            else broken["equipment"][0]["connectors"][0]
        )
        target.update(bad)
        ingest(db, s.source, s.operator_id, [good, broken])
    with SessionLocal() as db:
        assert db.get(Station, accounts[2][0]).name == "隔离测试"
        assert not db.scalar(select(Equipment.id).where(Equipment.station_id == accounts[2][0]))


def test_database_rejects_invalid_equipment_type_and_source_date(accounts, devices):
    with SessionLocal.begin() as db:
        e = Equipment(station_id=accounts[2][0], external_id="constraint", name="test", power_kw=1)
        db.add(e)
        db.flush()
        eid = e.id
    for assignment in ["equipment_type='guessed'", "manufactured_on=DATE '1800-01-01'"]:
        with pytest.raises(IntegrityError), SessionLocal.begin() as db:
            db.execute(text("UPDATE equipment SET " + assignment + " WHERE id=:id"), {"id": eid})


def test_extended_station_classes_reach_map_filters_and_options(accounts, devices):
    names, password, sids = accounts
    cases = {
        "usage_type": ["car_sharing", "community_shared", "workplace", "private_shared"],
        "construction_site": ["scenic_area", "bus_depot", "fuel_station", "taxi_site"],
    }
    with ExitStack() as stack:
        client = login(stack, names[0], password)
        options = client.get("/api/v1/map/options").json()
        for field, choices in cases.items():
            for value in choices:
                with SessionLocal.begin() as db:
                    s = db.get(Station, sids[0])
                    ingest(db, s.source, s.operator_id, [payload(s, **{field: value})])
                assert value in options["classifications"][field]
                result = client.get(
                    "/api/v1/map/stations", params={field: value, "location": "all"}
                ).json()
                assert result["total"] == 1 and result["items"][0][field] == value
                detail = client.get(f"/api/v1/stations/{sids[0]}").json()
                assert detail[field] == value
