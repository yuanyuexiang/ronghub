import csv
import io
import secrets
from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.exc import IntegrityError

from app import subsidy_device_days as service
from app.db import SessionLocal, engine
from app.main import app
from app.models import Audit, Equipment, Station, SubsidyDeviceDay, User


def sample(**changes):
    return {
        "external_id": "day-1",
        "equipment_external_id": "device-1",
        "day": "2026-11-01",
        "equipment_name": "=SOURCE DEVICE",
        "equipment_type": "dc",
        "power_kw": "120.00",
        "production_date": "2025-01-01",
        "business_kind": "charging",
        "energy_kwh": "42.125000",
        "energy_complete": True,
        "metric_basis": "calendar_day_seconds_v1",
        "fault_observed_seconds": "86400",
        "fault_unknown_seconds": "0",
        "fault_seconds": "3600",
        "offline_observed_seconds": "86400",
        "offline_unknown_seconds": "0",
        "offline_seconds": "0",
        "source_updated_at": "2026-11-02T01:00:00+08:00",
        **changes,
    }


@pytest.fixture()
def evidence(accounts):
    with SessionLocal.begin() as db:
        station = db.get(Station, accounts[2][0])
        oid = station.operator_id
        device = Equipment(
            station_id=station.id, external_id="device-1", name="current device", power_kw="120"
        )
        db.add(device)
        db.flush()
        eid = device.id
    yield accounts[2][0], oid, eid
    with SessionLocal.begin() as db:
        db.execute(delete(SubsidyDeviceDay).where(SubsidyDeviceDay.station_id.in_(accounts[2])))
        db.execute(delete(Equipment).where(Equipment.id == eid))


def put(evidence, rows, source="test", demo=True):
    with SessionLocal.begin() as db:
        return service.ingest(
            db, source, evidence[1], db.get(Station, evidence[0]), rows, is_demo=demo
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"energy_complete": True, "energy_kwh": None},
        {"energy_kwh": "NaN"},
        {"energy_kwh": "-1"},
        {"fault_seconds": "90000"},
        {"fault_unknown_seconds": "84000"},
        {"offline_observed_seconds": None},
        {"production_date": "2026-11-02"},
        {"source_updated_at": "2026-11-01T12:00:00+08:00"},
        {"energy_complete": "false"},
        {"metric_basis": "formal_rate"},
        {"source_updated_at": "2026-11-02T01:00:00"},
    ],
)
def test_source_validation_never_accepts_ambiguous_complete_days(changes):
    with pytest.raises(ValidationError):
        service.SourceDeviceDay.model_validate(sample(**changes))


def test_revisions_exact_mapping_atomicity_and_database_composite_ownership(evidence, accounts):
    assert put(evidence, [sample()])["changed"] == 1
    assert put(evidence, [sample()])["changed"] == 0
    correction = sample(energy_kwh="50", source_updated_at="2026-11-03T01:00:00+08:00")
    assert put(evidence, [correction])["changed"] == 1
    assert put(evidence, [sample()])["ignored_stale"] == 1
    with pytest.raises(ValueError):
        put(evidence, [{**correction, "energy_kwh": "60"}])
    with pytest.raises(ValueError):
        put(evidence, [{**correction, "day": "2026-11-02"}])
    with pytest.raises(ValueError):
        put(evidence, [correction], demo=False)
    with pytest.raises(ValueError):
        put(evidence, [correction], source="foreign")
    with pytest.raises(ValueError):
        put(
            evidence,
            [
                sample(
                    external_id="fresh",
                    day="2026-11-02",
                    energy_kwh="0",
                    source_updated_at="2026-11-03T01:00:00+08:00",
                ),
                sample(external_id="bad", equipment_external_id="other-device"),
            ],
        )
    with SessionLocal() as db:
        rows = list(
            db.scalars(select(SubsidyDeviceDay).where(SubsidyDeviceDay.station_id == evidence[0]))
        )
        assert len(rows) == 1 and rows[0].version == 2 and rows[0].energy_kwh == 50
        rid = rows[0].id
    with pytest.raises(IntegrityError), SessionLocal.begin() as db:
        db.execute(
            update(SubsidyDeviceDay)
            .where(SubsidyDeviceDay.id == rid)
            .values(station_id=accounts[2][1])
        )
    with pytest.raises(IntegrityError), SessionLocal.begin() as db:
        db.execute(
            update(SubsidyDeviceDay)
            .where(SubsidyDeviceDay.id == rid)
            .values(operator_id=evidence[1] + 1000000)
        )
    with pytest.raises(IntegrityError), SessionLocal.begin() as db:
        db.execute(
            update(SubsidyDeviceDay)
            .where(SubsidyDeviceDay.id == rid)
            .values(fault_seconds=Decimal("NaN"))
        )


def test_scoped_queries_null_zero_basis_exports_and_repeatable_read(
    evidence, accounts, staff, monkeypatch
):
    rows = [
        sample(),
        sample(
            external_id="day-2",
            day="2026-11-02",
            energy_kwh="0",
            source_updated_at="2026-11-04T01:00:00+08:00",
            fault_seconds="0",
        ),
        sample(
            external_id="day-3",
            day="2026-11-03",
            energy_kwh=None,
            energy_complete=False,
            fault_observed_seconds="43200",
            fault_seconds="0",
            offline_unknown_seconds="120",
            source_updated_at="2026-11-04T01:00:00+08:00",
        ),
    ]
    put(evidence, rows)
    clients = [TestClient(app, client=("device-days-" + secrets.token_hex(6), 1)) for _ in range(4)]
    try:
        for client, name in zip(
            clients, [accounts[0][0], accounts[0][1], staff[0][0], staff[0][1]], strict=True
        ):
            assert (
                client.post(
                    "/api/v1/auth/login", json={"username": name, "password": accounts[1]}
                ).status_code
                == 200
            )
        owner, other, reg, assoc = clients
        path = "/api/v1/subsidy/device-days"
        query = {"start_date": "2026-11-01", "end_date": "2026-11-03", "station_id": evidence[0]}
        result = owner.get(path, params=query)
        assert result.status_code == 200, result.text
        data = result.json()
        assert data["total"] == 3
        by_day = {row["day"]: row for row in data["items"]}
        assert by_day["2026-11-01"]["fault_percent"] == "4.166667"
        assert (
            by_day["2026-11-02"]["energy_kwh"] == "0.000000"
            and by_day["2026-11-02"]["fault_percent"] == "0.000000"
        )
        last = by_day["2026-11-03"]
        assert (
            last["energy_kwh"] is None
            and last["fault_percent"] is None
            and last["fault_percent_reason"] == "partial"
            and last["offline_percent_reason"] == "unknown_states"
        )
        assert all(
            not row["settlement_verified"]
            and row["mapping_basis"] == "source_station_equipment_key"
            for row in data["items"]
        )
        assert other.get(path, params=query).json()["total"] == 0
        assert assoc.get(path, params=query).status_code == 403
        assert owner.get(path, params={**query, "end_date": "2025-11-01"}).status_code == 422
        assert owner.get(path, params={**query, "end_date": "2027-11-01"}).status_code == 200
        assert owner.get(path, params={**query, "end_date": "2027-11-02"}).status_code == 422
        exported = owner.get(path + "/export", params=query)
        assert exported.status_code == 200
        assert (
            "'=SOURCE DEVICE" in exported.text
            and "4.166667" in exported.text
            and "未核验，不可结算" in exported.text
        )
        exported_rows = list(csv.DictReader(io.StringIO(exported.text.lstrip("\ufeff"))))
        assert (
            next(row for row in exported_rows if row["日期（北京时间）"] == "2026-11-01")[
                "故障秒数"
            ]
            == "3600.000000"
        )
        assert (
            next(row for row in exported_rows if row["日期（北京时间）"] == "2026-11-03")[
                "故障上报秒数"
            ]
            == "43200.000000"
        )
        assert other.get(path + "/export", params=query).text.count("\n") == 1
        assert assoc.get(path + "/export", params=query).status_code == 403
        from app.routers import subsidy_device_days as routes

        with SessionLocal() as db:
            audits_before = db.scalar(
                select(func.count())
                .select_from(Audit)
                .where(Audit.action == "subsidy.device_days.export")
            )
        with monkeypatch.context() as patch:
            patch.setattr(routes, "EXPORT_LIMIT", 1)
            assert owner.get(path + "/export", params=query).status_code == 422
        with SessionLocal() as db:
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(Audit)
                    .where(Audit.action == "subsidy.device_days.export")
                )
                == audits_before
            )
        changed = False

        def revise_after_count(connection, cursor, statement, parameters, context, executemany):
            nonlocal changed
            if not changed and "count(*)" in statement and "subsidy_device_days" in statement:
                changed = True
                with SessionLocal.begin() as db:
                    db.execute(
                        delete(SubsidyDeviceDay).where(
                            SubsidyDeviceDay.station_id == evidence[0],
                            SubsidyDeviceDay.day == datetime(2026, 11, 3).date(),
                        )
                    )

        event.listen(engine, "after_cursor_execute", revise_after_count)
        try:
            snapshot = owner.get(path, params=query).json()
        finally:
            event.remove(engine, "after_cursor_execute", revise_after_count)
        assert changed and snapshot["total"] == len(snapshot["items"]) == 3
        with SessionLocal.begin() as db:
            target = db.scalar(select(User).where(User.username == staff[0][0]))
            target.allowed_districts = []
        assert reg.get(path, params=query).json()["total"] == 0
        with SessionLocal.begin() as db:
            target = db.scalar(select(User).where(User.username == staff[0][0]))
            target.allowed_districts = ["测试"]
        assert reg.get(path, params=query).json()["total"] == 2
        put(
            evidence,
            [
                sample(
                    external_id="day-2",
                    day="2026-11-02",
                    energy_kwh="0",
                    metric_basis="unconfirmed",
                    source_updated_at="2026-11-05T01:00:00+08:00",
                )
            ],
        )
        unknown = owner.get(path, params={**query, "start_date": "2026-11-02"}).json()["items"][0]
        assert (
            unknown["fault_percent"] is None
            and unknown["fault_percent_reason"] == "basis_unconfirmed"
        )
    finally:
        for client in clients:
            client.close()
        with SessionLocal.begin() as db:
            db.execute(
                delete(Audit).where(
                    Audit.action == "subsidy.device_days.export",
                    Audit.user_id.in_(
                        select(User.id).where(
                            User.username.in_([accounts[0][0], accounts[0][1], staff[0][0]])
                        )
                    ),
                )
            )
