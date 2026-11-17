from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import delete, select

from app.alert_period_contracts import SourcePeriod, aggregate
from app.alert_period_facts import ingest
from app.db import SessionLocal
from app.models import AlertPeriodFact, Equipment, Station


def record(**changes):
    result = {
        "external_id": "week-fault",
        "metric": "station_fault",
        "period": "natural_week",
        "period_start": "2026-10-05",
        "period_end": "2026-10-12",
        "basis": "source_declared_equipment_service_seconds_v1",
        "equipment_external_ids": ["historical-1"],
        "members_complete": True,
        "members": [
            {
                "equipment_external_id": "historical-1",
                "equipment_name": "历史设备",
                "expected_seconds": "604800",
                "observed_seconds": "604800",
                "unknown_seconds": "0",
                "affected_seconds": "0",
            }
        ],
        "source_updated_at": "2026-10-12T00:00:00+08:00",
    }
    result.update(changes)
    return result


def test_independent_quality_and_known_zero():
    value = SourcePeriod.model_validate(record())
    assert aggregate(value) == (Decimal(0), Decimal(604800), None)
    value.members[0].unknown_seconds = Decimal(1)
    assert aggregate(value)[2] == "unknown_states"
    value.members[0].unknown_seconds = Decimal(0)
    value.members[0].observed_seconds = Decimal(500000)
    assert aggregate(value)[2] == "partial_coverage"
    value.members_complete = False
    assert aggregate(value)[2] == "incomplete_members"
    assert (
        aggregate(SourcePeriod.model_validate(record(equipment_external_ids=[], members=[])))[2]
        == "no_service_denominator"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"period_start": "2026-10-06"},
        {"period_end": "2026-10-13"},
        {"source_updated_at": "2026-10-11T23:59:59+08:00"},
        {"source_updated_at": "2026-10-12T00:00:00"},
        {"equipment_external_ids": ["historical-1", "historical-1"]},
        {"equipment_external_ids": []},
    ],
)
def test_reject_invalid_source_period(changes):
    with pytest.raises(ValidationError):
        SourcePeriod.model_validate(record(**changes))


@pytest.fixture()
def source(accounts):
    sid = accounts[2][0]
    with SessionLocal.begin() as db:
        station = db.get(Station, sid)
        owner = station.operator_id
        db.add(Equipment(station_id=sid, external_id="historical-1", name="历史设备", power_kw=10))
    yield {"station_id": sid, "operator_id": owner, "source": "test"}
    with SessionLocal.begin() as db:
        db.execute(delete(AlertPeriodFact).where(AlertPeriodFact.station_id == sid))
        db.execute(delete(Equipment).where(Equipment.station_id == sid))


def test_revision_idempotency_conflict_and_atomic_batch(source):
    with SessionLocal.begin() as db:
        assert ingest(db, **source, records=[record()])["created"] == 1
        equivalent = record()
        equivalent["members"][0]["affected_seconds"] = "0.000000"
        assert ingest(db, **source, records=[equivalent])["unchanged"] == 1
        changed = record()
        changed["members"][0]["affected_seconds"] = "10"
        with pytest.raises(HTTPException) as error:
            ingest(db, **source, records=[changed])
        assert error.value.status_code == 409
        changed["source_updated_at"] = "2026-10-13T00:00:00+08:00"
        assert ingest(db, **source, records=[changed])["updated"] == 1
        assert ingest(db, **source, records=[record()])["stale"] == 1
        fact = db.scalar(
            select(AlertPeriodFact).where(AlertPeriodFact.station_id == source["station_id"])
        )
        assert fact.version == 2
        assert fact.source_updated_at == datetime(2026, 10, 12, 16, tzinfo=UTC)
        first = record(external_id="utilization", metric="station_utilization")
        bad = record(
            external_id="offline",
            metric="station_offline",
            equipment_external_ids=["unmapped"],
            members=[],
            members_complete=False,
        )
        with pytest.raises(HTTPException):
            ingest(db, **source, records=[first, bad])
        assert (
            db.scalar(
                select(AlertPeriodFact.id).where(AlertPeriodFact.external_id == "utilization")
            )
            is None
        )
        with pytest.raises(HTTPException):
            ingest(
                db, **{**source, "operator_id": source["operator_id"] + 100000}, records=[record()]
            )


def test_scoped_source_api_keeps_unknown_and_denies_association(source, accounts, staff):
    import secrets

    from fastapi.testclient import TestClient

    from app.main import app

    with SessionLocal.begin() as db:
        payload = record()
        payload["members"][0]["unknown_seconds"] = "1"
        ingest(db, **source, records=[payload])
    users = [staff[0][0], accounts[0][0], accounts[0][1], staff[0][1]]
    for index, username in enumerate(users):
        with TestClient(app, client=("period-" + secrets.token_hex(6), 1)) as client:
            login = client.post(
                "/api/v1/auth/login", json={"username": username, "password": accounts[1]}
            )
            assert login.status_code == 200
            response = client.get(
                "/api/v1/alerts/period-facts", params={"station_id": source["station_id"]}
            )
            if index == 3:
                assert response.status_code == 403
                continue
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "no-store"
            assert response.json()["total"] == (0 if index == 2 else 1)
            if index != 2:
                row = response.json()["items"][0]
                assert row["percent"] is None and row["denominator_seconds"] is None
                assert row["quality_reason"] == "unknown_states" and row["verified"] is False
            assert client.get("/api/v1/alerts/period-facts?page_size=101").status_code == 422
