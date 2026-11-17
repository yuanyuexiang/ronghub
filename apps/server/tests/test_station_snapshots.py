import secrets
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, select

from app import workflows
from app.db import SessionLocal, engine
from app.main import app
from app.models import Connector, EnergyRecord, Equipment, Station, StationApplication, User


@pytest.fixture()
def reader(accounts):
    names, password, stations = accounts
    with SessionLocal.begin() as db:
        station = db.get(Station, stations[0])
        station.name = "reader-old"
        uid = db.scalar(select(User.id).where(User.username == names[0]))
        db.add(
            EnergyRecord(
                station_id=station.id,
                source="snapshot-fixture",
                external_id=secrets.token_hex(16),
                day=date(2026, 11, 1),
                kwh=Decimal("5.123456"),
            )
        )
        for index in range(3):
            device = Equipment(
                station_id=station.id,
                external_id=f"equipment-{index}",
                name=f"old-equipment-{index}",
                power_kw=60,
            )
            db.add(device)
            db.flush()
            db.add(Connector(equipment_id=device.id, external_id="gun-1", status="available"))
        application = workflows.create(
            db, db.get(User, uid), station.id, "access", {"note": "Original"}
        )
        aid = application.id
    try:
        with TestClient(app, client=("snapshot-" + secrets.token_hex(8), 1)) as client:
            assert (
                client.post(
                    "/api/v1/auth/login", json={"username": names[0], "password": password}
                ).status_code
                == 200
            )
            yield {"client": client, "sid": stations[0], "aid": aid, "uid": uid}
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(EnergyRecord).where(EnergyRecord.station_id == stations[0]))
            db.execute(
                delete(Connector).where(
                    Connector.equipment_id.in_(
                        select(Equipment.id).where(Equipment.station_id == stations[0])
                    )
                )
            )
            db.execute(delete(Equipment).where(Equipment.station_id == stations[0]))


@pytest.mark.parametrize(
    "view", ["stations", "dashboard", "equipment", "applications", "application_detail"]
)
def test_inventory_and_application_views_keep_one_snapshot_during_committed_source_and_workflow_changes(
    reader, view
):
    changed = False

    def concurrent_commit(connection, cursor, statement, parameters, context, executemany):
        nonlocal changed
        match = {
            "stations": "FROM stations" in statement and "count(" in statement,
            "dashboard": statement.lstrip().startswith("SELECT stations.")
            and "FROM stations" in statement,
            "equipment": statement.lstrip().startswith("SELECT equipment.")
            and "FROM equipment" in statement,
            "applications": "FROM station_applications" in statement and "count(" in statement,
            "application_detail": statement.lstrip().startswith("SELECT station_applications."),
        }[view]
        if not changed and match:
            changed = True
            with SessionLocal.begin() as other:
                station = other.get(Station, reader["sid"])
                station.name, station.power_kw, station.connectors = "reader-new", Decimal(480), 8
                energy = other.scalar(
                    select(EnergyRecord).where(EnergyRecord.station_id == station.id)
                )
                energy.kwh = Decimal(99)
                device = other.scalar(
                    select(Equipment)
                    .where(Equipment.station_id == station.id)
                    .order_by(Equipment.id)
                )
                device.name = "new-equipment-0"
                other.scalar(
                    select(Connector).where(Connector.equipment_id == device.id)
                ).status = "fault"
                workflows.act(other, other.get(User, reader["uid"]), reader["aid"], "submit", 1, "")

    path, params = {
        "stations": ("/api/v1/stations", {"q": "reader-old"}),
        "dashboard": ("/api/v1/dashboard", {}),
        "equipment": (f"/api/v1/stations/{reader['sid']}/equipment", {}),
        "applications": ("/api/v1/applications", {"status": "draft", "q": "reader-old"}),
        "application_detail": (f"/api/v1/applications/{reader['aid']}", {}),
    }[view]
    event.listen(engine, "after_cursor_execute", concurrent_commit)
    try:
        response = reader["client"].get(path, params=params)
    finally:
        event.remove(engine, "after_cursor_execute", concurrent_commit)
    assert changed and response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    if view == "stations":
        assert result["total"] == 1 and len(result["items"]) == 1
        assert result["items"][0]["name"] == "reader-old" and result["items"][0]["connectors"] == 2
    elif view == "dashboard":
        assert result["stations"] == 1 and result["connectors"] == 2
        assert Decimal(result["power_kw"]) == Decimal(120)
        assert result["energy_total"] == "5.123456"
        assert (
            result["applications_draft"] == 1
            and result["applications_pending"] == 0
            and result["pending"] == 0
        )
    elif view == "equipment":
        assert len(result["items"]) == 3
        assert result["items"][0]["name"] == "old-equipment-0"
        assert all(item["connectors"][0]["status"] == "available" for item in result["items"])
    else:
        if view == "applications":
            assert result["total"] == 1 and len(result["items"]) == 1
            result = result["items"][0]
        else:
            assert [item["action"] for item in result["history"]] == ["create"]
        assert (
            result["status"] == "draft"
            and result["version"] == 1
            and result["station_name"] == "reader-old"
        )
    with SessionLocal() as db:
        assert db.get(Station, reader["sid"]).name == "reader-new"
        assert db.get(StationApplication, reader["aid"]).status == "submitted"


def test_station_name_search_is_literal_and_large_pagination_is_rejected(reader):
    client = reader["client"]
    assert client.get("/api/v1/stations", params={"q": "%"}).json()["total"] == 0
    with SessionLocal.begin() as db:
        db.get(Station, reader["sid"]).name = "literal%_\\station"
    assert client.get("/api/v1/stations", params={"q": "%_\\"}).json()["total"] == 1
    for params in [{"page": 2147483648}, {"operator_id": 2147483648}, {"operator_id": 0}]:
        assert client.get("/api/v1/stations", params=params).status_code == 422
