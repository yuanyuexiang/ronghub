import secrets
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.main import app
from app.models import RoleProfile, Station, User
from app.station_facilities import FIELDS
from app.sync import ingest


def record(station, **extra):
    return {
        "external_id": station.external_id,
        "name": station.name,
        "district": station.district,
        "address": station.address,
        "power_kw": "120.00",
        "equipment": [],
        **extra,
    }


def sample():
    return {
        "supply_type": "direct",
        "electricity_tariff": "commercial",
        "supply_capacity": "100.125",
        "supply_capacity_unit": "unknown",
        "independent_connection": False,
        "parking_spaces": 0,
        "commissioned_on": "2024-02-29",
        "parking_fee_type": "charging_discount",
        "parking_fee_description": "充电限时减免，以场地公示为准",
        "business_hours": "00:00–24:00",
        "filing_code": "DEMO-备案-001",
        "station_contact_name": "演示联系人",
        "station_contact_phone": "400-000-0000",
    }


def test_source_facilities_idempotency_omission_clear_and_status_independence(accounts):
    _, _, ids = accounts
    with SessionLocal.begin() as db:
        s = db.get(Station, ids[0])
        assert all(getattr(s, key) is None for key in FIELDS)
        s.status = "accepted"
        payload = record(s, **sample())
        source, operator_id = s.source, s.operator_id
        ingest(db, source, operator_id, [payload, payload])
    with SessionLocal.begin() as db:
        s = db.get(Station, ids[0])
        assert s.supply_capacity == Decimal("100.125") and s.supply_capacity_unit == "unknown"
        assert s.independent_connection is False and s.parking_spaces == 0
        assert s.commissioned_on == date(2024, 2, 29)
        assert s.status == "accepted" and s.source_first_connected_at is None
        ingest(db, source, operator_id, [record(s, name="Partial source update")])
    with SessionLocal.begin() as db:
        s = db.get(Station, ids[0])
        assert s.supply_capacity == Decimal("100.125") and s.parking_spaces == 0
        assert s.station_contact_phone == "400-000-0000"
        ingest(db, source, operator_id, [record(s, **dict.fromkeys(FIELDS))])
    with SessionLocal() as db:
        s = db.get(Station, ids[0])
        assert all(getattr(s, key) is None for key in FIELDS)
        assert s.status == "accepted" and s.source_first_connected_at is None


def test_detail_contact_capabilities_and_company_region_isolation(accounts, staff):
    names, password, ids = accounts
    clients = [TestClient(app, client=("facility-" + secrets.token_hex(5), 1)) for _ in range(4)]
    profile_id = None
    try:
        with SessionLocal.begin() as db:
            s = db.get(Station, ids[0])
            s.district = "facility-" + secrets.token_hex(5)
            ingest(db, s.source, s.operator_id, [record(s, **sample())])
        for client, name in zip(clients, [*names, *staff[0]], strict=True):
            assert (
                client.post(
                    "/api/v1/auth/login", json={"username": name, "password": password}
                ).status_code
                == 200
            )
        owner, other, regulator, association = clients
        path = f"/api/v1/stations/{ids[0]}"
        own = owner.get(path)
        assert own.status_code == 200
        data = own.json()
        assert data["facilities"]["supply_capacity"] == "100.125"
        assert data["facilities"]["independent_connection"] is False
        assert data["facilities"]["parking_spaces"] == 0
        assert data["facilities"]["commissioned_on"] == "2024-02-29"
        assert data["contact_accessible"] is True
        assert data["contact"] == {"name": "演示联系人", "phone": "400-000-0000"}
        assert other.get(path).status_code == 404
        assert regulator.get(path).json()["contact"] == data["contact"]
        denied = association.get(path).json()
        assert denied["contact_accessible"] is False and denied["contact"] is None
        assert "400-000-0000" not in association.get("/api/v1/map/stations").text
        with SessionLocal.begin() as db:
            user = db.scalar(select(User).where(User.username == names[0]))
            profile = RoleProfile(
                name="facility-" + secrets.token_hex(8),
                base_role="operator",
                permissions=["stations.read"],
            )
            db.add(profile)
            db.flush()
            profile_id = profile.id
            user.role_profile_id = profile_id
        limited = owner.get(path).json()
        assert limited["contact"] is None and limited["contact_accessible"] is False
        assert limited["facilities"] == data["facilities"]
        with SessionLocal.begin() as db:
            user = db.scalar(select(User).where(User.username == staff[0][0]))
            user.allowed_districts = []
        assert regulator.get(path).status_code == 404
    finally:
        for client in clients:
            client.close()
        with SessionLocal.begin() as db:
            user = db.scalar(select(User).where(User.username == names[0]))
            user.role_profile_id = None
            db.flush()
            if profile_id:
                db.execute(delete(RoleProfile).where(RoleProfile.id == profile_id))


@pytest.mark.parametrize(
    "bad",
    [
        {"supply_type": "1"},
        {"electricity_tariff": True},
        {"supply_capacity": "1.0001", "supply_capacity_unit": "kVA"},
        {"supply_capacity": "NaN", "supply_capacity_unit": "kVA"},
        {"supply_capacity": "-1", "supply_capacity_unit": "kVA"},
        {"supply_capacity": "100000000000", "supply_capacity_unit": "kVA"},
        {"supply_capacity": 1.25, "supply_capacity_unit": "kVA"},
        {"supply_capacity": "1"},
        {"supply_capacity_unit": "kVA"},
        {"supply_capacity": None, "supply_capacity_unit": "kVA"},
        {"supply_capacity": "1", "supply_capacity_unit": "MW"},
        {"independent_connection": 0},
        {"parking_spaces": True},
        {"parking_spaces": -1},
        {"parking_spaces": 2147483648},
        {"commissioned_on": "2023-02-29"},
        {"commissioned_on": "2024-02-29T00:00:00+08:00"},
        {"commissioned_on": "20240229"},
        {"commissioned_on": "1800-01-01"},
        {"parking_fee_type": "255"},
        {"station_contact_phone": "1\n2"},
        {"parking_fee_description": "x" * 501},
    ],
)
def test_bad_source_payload_rolls_back_whole_batch(accounts, bad):
    _, _, ids = accounts
    with pytest.raises(ValueError), SessionLocal.begin() as db:
        s = db.get(Station, ids[0])
        ingest(
            db,
            s.source,
            s.operator_id,
            [record(s, name="must roll back", **sample()), record(s, **bad)],
        )
    with SessionLocal() as db:
        s = db.get(Station, ids[0])
        assert s.name == "隔离测试"
        assert all(getattr(s, key) is None for key in FIELDS)


@pytest.mark.parametrize(
    "assignment",
    [
        "supply_capacity = 1",
        "supply_capacity = 'NaN'::numeric, supply_capacity_unit = 'kVA'",
        "supply_capacity_unit = 'kVA'",
        "parking_spaces = -1",
        "supply_type = 'unmapped'",
        "commissioned_on = DATE '1800-01-01'",
    ],
)
def test_database_rejects_inconsistent_source_facilities(accounts, assignment):
    _, _, ids = accounts
    with pytest.raises(IntegrityError), SessionLocal.begin() as db:
        db.execute(text("UPDATE stations SET " + assignment + " WHERE id=:id"), {"id": ids[0]})
