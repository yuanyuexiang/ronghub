import copy
import json
import secrets
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from subsidy_samples import evidence as attach_evidence

from app import subsidy, subsidy_device_days, subsidy_device_scopes
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import (
    Audit,
    EnergyAllocation,
    EnergyRecord,
    Equipment,
    RatingAction,
    RatingApplication,
    RatingStandard,
    Station,
    SubsidyAction,
    SubsidyAnnualBatch,
    SubsidyAttachment,
    SubsidyClaim,
    SubsidyDeviceAllocation,
    SubsidyDeviceDay,
    SubsidyDeviceLine,
    SubsidyDeviceScope,
    SubsidyPolicy,
    User,
)
from app.subsidy_rating_history import at_period_end


def rules():
    return {
        "configuration_version": 2,
        "swap_rate": "0.200001",
        "generation_period": "day",
        "star_coefficients": {str(i): "1.200001" for i in range(6)},
        "fault_rate_below": "10",
        "offline_rate_below": "20",
        "metric_basis": "calendar_day_seconds_v1",
        "device_scope_basis": "source_declared_station_day_v1",
        "rating_reference": "period_end_publication_snapshot_v1",
        "rounding": "HALF_UP_PER_DEVICE_DAY_THEN_COEFFICIENT_2DP",
    }


@pytest.fixture()
def data(accounts, staff, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    with SessionLocal.begin() as db:
        station = db.get(Station, accounts[2][0])
        station.status = "operating"
        user = db.scalar(select(User).where(User.username == accounts[0][0]))
        uid = user.id
        actor = db.scalar(select(User).where(User.username == staff[0][1]))
        equipment = Equipment(
            station_id=station.id, external_id="device-1", name="device", power_kw="120"
        )
        db.add(equipment)
        other = Equipment(
            station_id=station.id,
            external_id="not-historically-in-service",
            name="current extra device",
            power_kw="7",
        )
        db.add(other)
        standard = RatingStandard(
            code="grade-" + secrets.token_hex(6),
            version=1,
            name="Demo history",
            rules={},
            is_demo=True,
            enabled=True,
        )
        db.add(standard)
        db.flush()
        stdid = standard.id
        publication = datetime(2026, 10, 1, tzinfo=UTC)
        grade = RatingApplication(
            station_id=station.id,
            operator_id=station.operator_id,
            standard_id=standard.id,
            status="published",
            version=4,
            initial_scores={},
            current_scores={},
            calculation={"stars": 5, "total": "96", "missing": []},
            rule_snapshot={},
            evidence_note="",
            published_at=publication,
            valid_until=date(2026, 12, 31),
            is_demo=True,
        )
        db.add(grade)
        db.flush()
        gid = grade.id
        grade.report_snapshot = {
            "report_id": f"RATE-{gid}-V4",
            "station": {"id": station.id},
            "published_at": publication.isoformat(),
            "valid_until": "2026-12-31",
            "calculation": {"stars": 5},
            "is_demo": True,
        }
        after = {
            "station_id": station.id,
            "operator_id": station.operator_id,
            "status": "published",
            "version": 4,
            "stars": 5,
            "published_at": publication.isoformat(),
            "valid_until": "2026-12-31",
            "is_demo": True,
        }
        event = RatingAction(
            application_id=gid,
            actor_id=actor.id,
            action="publish",
            comment="Synthetic historical demo publication",
            transition_snapshot={"before": None, "after": after},
            created_at=publication + timedelta(seconds=1),
        )
        db.add(event)
        policy = SubsidyPolicy(
            code="device-" + secrets.token_hex(6),
            version=1,
            name="Executable demo",
            rate="0.100001",
            effective_from=date(2025, 1, 1),
            effective_to=date(2026, 12, 31),
            device_rules=rules(),
            is_demo=True,
            enabled=True,
        )
        db.add(policy)
        db.flush()
        pid = policy.id
        scopes = [
            {
                "external_id": f"scope-{day}",
                "day": f"2026-11-0{day}",
                "equipment_external_ids": ["device-1"],
                "complete": True,
                "source_updated_at": "2026-11-05T01:00:00+08:00",
            }
            for day in [1, 2, 3]
        ]
        subsidy_device_scopes.ingest(db, "test", station.operator_id, station, scopes, is_demo=True)
        facts = [
            {
                "external_id": f"fact-{day}",
                "scope_external_id": f"scope-{day}",
                "equipment_external_id": "device-1",
                "day": f"2026-11-0{day}",
                "equipment_name": "source device",
                "equipment_type": "dc",
                "power_kw": "120",
                "production_date": "2025-01-01",
                "business_kind": "charging",
                "energy_kwh": kwh,
                "energy_complete": kwh is not None,
                "metric_basis": "calendar_day_seconds_v1",
                "fault_observed_seconds": "86400",
                "fault_unknown_seconds": "0",
                "fault_seconds": "0",
                "offline_observed_seconds": "86400",
                "offline_unknown_seconds": "0",
                "offline_seconds": "0",
                "source_updated_at": "2026-11-05T01:00:00+08:00",
            }
            for day, kwh in [(1, "42.125000"), (2, "0"), (3, None)]
        ]
        subsidy_device_days.ingest(db, "test", station.operator_id, station, facts, is_demo=True)
    yield {
        "station": accounts[2][0],
        "user": uid,
        "policy": pid,
        "grade": gid,
        "standard": stdid,
        "facts": facts,
        "scopes": scopes,
    }
    with SessionLocal.begin() as db:
        db.execute(delete(SubsidyAnnualBatch).where(SubsidyAnnualBatch.actor_id == uid))
        cids = select(SubsidyClaim.id).where(SubsidyClaim.station_id == accounts[2][0])
        db.execute(
            delete(SubsidyDeviceAllocation).where(SubsidyDeviceAllocation.claim_id.in_(cids))
        )
        db.execute(delete(SubsidyDeviceLine).where(SubsidyDeviceLine.claim_id.in_(cids)))
        db.execute(delete(EnergyAllocation).where(EnergyAllocation.claim_id.in_(cids)))
        db.execute(delete(SubsidyAttachment).where(SubsidyAttachment.claim_id.in_(cids)))
        db.execute(delete(SubsidyAction).where(SubsidyAction.claim_id.in_(cids)))
        db.execute(delete(SubsidyClaim).where(SubsidyClaim.id.in_(cids)))
        db.execute(delete(SubsidyDeviceDay).where(SubsidyDeviceDay.station_id == accounts[2][0]))
        db.execute(
            delete(SubsidyDeviceScope).where(SubsidyDeviceScope.station_id == accounts[2][0])
        )
        db.execute(delete(EnergyRecord).where(EnergyRecord.station_id == accounts[2][0]))
        db.execute(delete(Equipment).where(Equipment.station_id == accounts[2][0]))
        db.execute(delete(RatingAction).where(RatingAction.application_id == gid))
        db.execute(delete(RatingApplication).where(RatingApplication.id == gid))
        db.execute(delete(RatingStandard).where(RatingStandard.id == stdid))
        db.execute(delete(SubsidyPolicy).where(SubsidyPolicy.id == pid))


def create(db, data, start=date(2026, 11, 1), end=date(2026, 11, 2)):
    return subsidy.create_claim(
        db,
        db.get(User, data["user"]),
        data["station"],
        data["policy"],
        start,
        end,
        "Device evidence statement",
    )


def test_end_to_end_frozen_device_money_materials_review_and_released_allocation(
    data, staff, tmp_path
):
    with SessionLocal.begin() as db:
        claim = create(db, data)
        cid = claim.id
        assert (
            claim.total_kwh == Decimal("42.125")
            and claim.base_amount == Decimal("4.21")
            and claim.adjusted_amount == Decimal("5.05")
        )
        assert claim.snapshot["device_calculation"]["row_count"] == 2
        attach_evidence(db, claim, db.get(User, data["user"]))
    with SessionLocal.begin() as db:
        claim = db.get(SubsidyClaim, cid)
        subsidy.act(db, db.get(User, data["user"]), cid, "submit", claim.version, "submit")
    with SessionLocal.begin() as db:
        reg = db.scalar(select(User).where(User.username == staff[0][0]))
        claim = db.get(SubsidyClaim, cid)
        subsidy.act(db, reg, cid, "adjust", claim.version, "manual difference", Decimal(".01"))
    with SessionLocal.begin() as db:
        reg = db.scalar(select(User).where(User.username == staff[0][0]))
        claim = db.get(SubsidyClaim, cid)
        subsidy.act(db, reg, cid, "approve", claim.version, "checked frozen evidence")
    with SessionLocal.begin() as db:
        reg = db.scalar(select(User).where(User.username == staff[0][0]))
        claim = db.get(SubsidyClaim, cid)
        subsidy.act(db, reg, cid, "record_disbursement", claim.version, "demo status only")
        assert claim.adjusted_amount == Decimal("5.06") and claim.status == "disbursed"
        assert (
            db.scalar(
                select(func.count())
                .select_from(SubsidyDeviceAllocation)
                .where(SubsidyDeviceAllocation.claim_id == cid)
            )
            == 2
        )
    from app.charging_report import collect as combined

    with SessionLocal() as db:
        report = combined(
            db, db.get(User, data["user"]), "month", date(2026, 11, 1), "equipment", "", 1, 20
        )
        allocated = [item for item in report.items if item.subsidies]
        assert len(allocated) == 1 and Decimal(allocated[0].subsidies[0].base_amount) == Decimal(
            "4.21"
        )
        assert (
            allocated[0].subsidies[0].adjusted_amount is None
            and allocated[0].subsidies[0].disbursed_amount is None
        )
        assert Decimal(report.subsidies[0].adjusted_amount) == Decimal("5.06")
        assert Decimal(report.subsidies[0].disbursed_amount) == Decimal("5.06")
        from app.charging_report_excel import workbook

        (tmp_path / "device-report.xlsx").write_bytes(workbook(report))
    with pytest.raises(HTTPException) as error, SessionLocal.begin() as db:
        create(db, data)
    assert error.value.status_code == 409


def test_unknown_scope_or_partial_day_is_blocked_and_declared_scope_is_not_current_inventory(data):
    with SessionLocal.begin() as db:
        with pytest.raises(HTTPException) as error:
            create(db, data, end=date(2026, 11, 3))
        assert "未知" in error.value.detail
    with SessionLocal.begin() as db:
        scope = db.scalar(
            select(SubsidyDeviceScope).where(
                SubsidyDeviceScope.station_id == data["station"],
                SubsidyDeviceScope.day == date(2026, 11, 2),
            )
        )
        scope.complete = False
    with pytest.raises(HTTPException), SessionLocal.begin() as db:
        create(db, data)
    with SessionLocal.begin() as db:
        scope = db.scalar(
            select(SubsidyDeviceScope).where(
                SubsidyDeviceScope.station_id == data["station"],
                SubsidyDeviceScope.day == date(2026, 11, 2),
            )
        )
        scope.complete = True
        assert create(db, data).snapshot["device_calculation"]["row_count"] == 2


def test_later_delisting_is_not_retroactive_but_period_end_delisting_blocks(data, staff):
    with SessionLocal.begin() as db:
        grade = db.get(RatingApplication, data["grade"])
        grade.status = "delisted"
        grade.version += 1
        actor = db.scalar(select(User.id).where(User.username == staff[0][1]))
        event = RatingAction(
            application_id=grade.id,
            actor_id=actor,
            action="delist",
            comment="later",
            created_at=datetime(2026, 11, 10, tzinfo=UTC),
            transition_snapshot={"after": {"status": "delisted"}},
        )
        db.add(event)
        db.flush()
        eid = event.id
        assert at_period_end(db, db.get(Station, data["station"]), date(2026, 11, 2)).stars == 5
        assert create(db, data).adjusted_amount == Decimal("5.05")
    with SessionLocal.begin() as db:
        event = db.get(RatingAction, eid)
        event.created_at = datetime(2026, 11, 1, tzinfo=UTC)
    with pytest.raises(HTTPException), SessionLocal() as db:
        at_period_end(db, db.get(Station, data["station"]), date(2026, 11, 2))


def test_revision_does_not_rewrite_draft_and_withdraw_releases_for_reapply(data):
    with SessionLocal.begin() as db:
        claim = create(db, data)
        cid = claim.id
        before = copy.deepcopy(claim.snapshot)
        attach_evidence(db, claim, db.get(User, data["user"]))
    corrected = {
        **data["facts"][0],
        "energy_kwh": "50",
        "source_updated_at": "2026-11-06T01:00:00+08:00",
    }
    with SessionLocal.begin() as db:
        subsidy_device_days.ingest(
            db,
            "test",
            db.get(Station, data["station"]).operator_id,
            db.get(Station, data["station"]),
            [corrected],
            is_demo=True,
        )
    with pytest.raises(HTTPException), SessionLocal.begin() as db:
        claim = db.get(SubsidyClaim, cid)
        subsidy.act(db, db.get(User, data["user"]), cid, "submit", claim.version, "stale")
    with SessionLocal.begin() as db:
        claim = db.get(SubsidyClaim, cid)
        assert claim.snapshot == before
        subsidy.act(
            db, db.get(User, data["user"]), cid, "withdraw", claim.version, "corrected source"
        )
    with SessionLocal.begin() as db:
        claim = create(db, data)
        assert claim.total_kwh == 50 and claim.adjusted_amount == Decimal("6.00")
        assert (
            db.scalar(
                select(func.count())
                .select_from(SubsidyDeviceLine)
                .where(SubsidyDeviceLine.claim_id == cid)
            )
            == 2
        )


def test_strict_threshold_boundary_and_no_missing_historical_grade_default(data):
    with SessionLocal.begin() as db:
        policy = db.get(SubsidyPolicy, data["policy"])
        policy.rate = Decimal("0.1")
        first = db.scalar(
            select(SubsidyDeviceDay).where(
                SubsidyDeviceDay.station_id == data["station"],
                SubsidyDeviceDay.day == date(2026, 11, 1),
            )
        )
        first.fault_seconds = Decimal(8640)
        second = db.scalar(
            select(SubsidyDeviceDay).where(
                SubsidyDeviceDay.station_id == data["station"],
                SubsidyDeviceDay.day == date(2026, 11, 2),
            )
        )
        second.energy_kwh = Decimal("8.25")
        second.business_kind = "swapping"
        claim = create(db, data)
        assert claim.base_amount == Decimal("1.65") and claim.adjusted_amount == Decimal("1.98")
        db.flush()
        line = db.get(SubsidyDeviceLine, (claim.id, first.id))
        assert (
            line.snapshot["eligible"] is False
            and line.snapshot["exclusion_reason"] == "fault_threshold"
            and line.snapshot["amount"] == "0.00"
        )
    with SessionLocal.begin() as db:
        event = db.scalar(select(RatingAction).where(RatingAction.application_id == data["grade"]))
        event.transition_snapshot = None
    with pytest.raises(HTTPException), SessionLocal() as db:
        at_period_end(db, db.get(Station, data["station"]), date(2026, 11, 2))


def test_api_typed_device_lines_are_paged_scoped_and_original_publication_is_frozen(
    data, accounts, staff
):
    clients = [TestClient(app, client=("execute-api-" + secrets.token_hex(6), 1)) for _ in range(3)]
    owner, other, reg = clients
    try:
        headers = []
        for client, name in zip(
            clients, [accounts[0][0], accounts[0][1], staff[0][0]], strict=True
        ):
            r = client.post("/api/v1/auth/login", json={"username": name, "password": accounts[1]})
            assert r.status_code == 200
            headers.append({"x-csrf-token": r.json()["csrf_token"]})
        policies = owner.get("/api/v1/subsidy/policies").json()["items"]
        selected = next(p for p in policies if p["id"] == data["policy"])
        assert selected["claim_supported"] and selected["calculation_basis"] == "demo_device_day_v2"
        response = owner.post(
            "/api/v1/subsidy/claims",
            headers=headers[0],
            json={
                "station_id": data["station"],
                "policy_id": data["policy"],
                "start_date": "2026-11-01",
                "end_date": "2026-11-02",
                "application_note": "frozen proof",
            },
        )
        assert response.status_code == 201, response.text
        cid = response.json()["id"]
        path = f"/api/v1/subsidy/claims/{cid}"
        detail = owner.get(path)
        assert detail.status_code == 200, detail.text
        assert detail.json()["snapshot"]["device_calculation"]["rating"]["stars"] == 5
        page = owner.get(path + "/device-lines", params={"page_size": 1}).json()
        assert page["total"] == 2 and len(page["items"]) == 1
        assert page["items"][0]["snapshot"]["source"]["energy_kwh"] == "42.125000"
        assert (
            owner.get(path + "/device-lines", params={"page": 2, "page_size": 1}).json()["items"][
                0
            ]["snapshot"]["source"]["energy_kwh"]
            == "0.000000"
        )
        assert other.get(path + "/device-lines").status_code == 404
        assert reg.get(path + "/device-lines").status_code == 200
        body = {
            "code": "v2-api-" + secrets.token_hex(6),
            "version": 1,
            "name": "Executable rule",
            "rate": "0.100001",
            "effective_from": "2026-01-01",
            "effective_to": "2026-12-31",
            "device_rules": rules(),
        }
        created = reg.post("/api/v1/subsidy/policies", headers=headers[2], json=body)
        assert created.status_code == 201, created.text
        with SessionLocal.begin() as db:
            db.execute(
                delete(Audit).where(
                    Audit.action == "subsidy.policy.create", Audit.object_id == created.json()["id"]
                )
            )
            db.execute(delete(SubsidyPolicy).where(SubsidyPolicy.id == created.json()["id"]))
    finally:
        for client in clients:
            client.close()


@pytest.mark.parametrize("direction", ["legacy-first", "device-first"])
def test_old_station_energy_and_new_device_measurements_never_double_claim_same_period(
    data, direction
):
    with SessionLocal.begin() as db:
        old = SubsidyPolicy(
            code="old-" + secrets.token_hex(6),
            version=1,
            name="old demo",
            rate="0.1",
            effective_from=date(2026, 1, 1),
            effective_to=date(2026, 12, 31),
            is_demo=True,
            enabled=True,
        )
        db.add(old)
        energy = EnergyRecord(
            station_id=data["station"],
            source="test",
            external_id=secrets.token_hex(6),
            day=date(2026, 11, 1),
            kwh="42.125",
            eligible=True,
        )
        db.add(energy)
        db.flush()
        pid = old.id
    try:
        with SessionLocal.begin() as db:
            user = db.get(User, data["user"])
            if direction == "legacy-first":
                subsidy.create_claim(
                    db, user, data["station"], pid, date(2026, 11, 1), date(2026, 11, 2)
                )
            else:
                create(db, data)
        with pytest.raises(HTTPException) as error, SessionLocal.begin() as db:
            if direction == "legacy-first":
                create(db, data)
            else:
                subsidy.create_claim(
                    db,
                    db.get(User, data["user"]),
                    data["station"],
                    pid,
                    date(2026, 11, 1),
                    date(2026, 11, 2),
                )
        assert error.value.status_code == 409
    finally:
        with SessionLocal.begin() as db:
            cids = select(SubsidyClaim.id).where(SubsidyClaim.policy_id == pid)
            db.execute(delete(EnergyAllocation).where(EnergyAllocation.claim_id.in_(cids)))
            db.execute(delete(SubsidyAction).where(SubsidyAction.claim_id.in_(cids)))
            db.execute(delete(SubsidyClaim).where(SubsidyClaim.policy_id == pid))
            db.delete(db.get(SubsidyPolicy, pid))


def test_projection_failure_rolls_back_frozen_lines_and_allocations(data, accounts, monkeypatch):
    client = TestClient(app, client=("device-projection-" + secrets.token_hex(6), 1))
    try:
        login = client.post(
            "/api/v1/auth/login", json={"username": accounts[0][0], "password": accounts[1]}
        )
        assert login.status_code == 200
        with SessionLocal() as db:
            before = [
                db.scalar(select(func.count()).select_from(model))
                for model in [SubsidyClaim, SubsidyDeviceLine, SubsidyDeviceAllocation]
            ]
        original = subsidy.output
        monkeypatch.setattr(
            subsidy, "output", lambda claim: {**original(claim), "base_amount": 4.21}
        )
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            client.post(
                "/api/v1/subsidy/claims",
                headers={"x-csrf-token": login.json()["csrf_token"]},
                json={
                    "station_id": data["station"],
                    "policy_id": data["policy"],
                    "start_date": "2026-11-01",
                    "end_date": "2026-11-02",
                },
            )
        with SessionLocal() as db:
            assert [
                db.scalar(select(func.count()).select_from(model))
                for model in [SubsidyClaim, SubsidyDeviceLine, SubsidyDeviceAllocation]
            ] == before
    finally:
        client.close()


def test_concurrent_claims_serialize_on_station_and_allocate_once(data):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)

    def attempt():
        try:
            with SessionLocal.begin() as db:
                db.get(User, data["user"])
                barrier.wait(timeout=10)
                claim = create(db, data)
                return claim.id
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert results.count(409) == 1
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(SubsidyClaim)
                .where(SubsidyClaim.station_id == data["station"])
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(SubsidyDeviceAllocation)
                .join(
                    SubsidyDeviceLine,
                    (SubsidyDeviceAllocation.claim_id == SubsidyDeviceLine.claim_id)
                    & (SubsidyDeviceAllocation.fact_id == SubsidyDeviceLine.fact_id),
                )
                .join(SubsidyClaim, SubsidyClaim.id == SubsidyDeviceLine.claim_id)
                .where(SubsidyClaim.station_id == data["station"])
            )
            == 2
        )


def test_closed_annual_create_and_same_receipt_replay_use_device_pipeline(data, accounts):
    first = date(2025, 1, 1)
    dates = [first + timedelta(days=i) for i in range(365)]
    with SessionLocal.begin() as db:
        station = db.get(Station, data["station"])
        grade = db.get(RatingApplication, data["grade"])
        stamp = datetime(2024, 12, 31, tzinfo=UTC)
        grade.published_at = stamp
        grade.report_snapshot = {**grade.report_snapshot, "published_at": stamp.isoformat()}
        event = db.scalar(select(RatingAction).where(RatingAction.application_id == grade.id))
        event.created_at = stamp + timedelta(seconds=1)
        event.transition_snapshot = {
            "before": None,
            "after": {**event.transition_snapshot["after"], "published_at": stamp.isoformat()},
        }
        scopes = [
            {
                "external_id": f"annual-scope-{day.isoformat()}",
                "day": day.isoformat(),
                "equipment_external_ids": ["device-1"],
                "complete": True,
                "source_updated_at": "2026-01-02T01:00:00+08:00",
            }
            for day in dates
        ]
        subsidy_device_scopes.ingest(db, "test", station.operator_id, station, scopes, is_demo=True)
        facts = [
            {
                **data["facts"][0],
                "external_id": f"annual-fact-{day.isoformat()}",
                "scope_external_id": f"annual-scope-{day.isoformat()}",
                "day": day.isoformat(),
                "energy_kwh": "1",
                "source_updated_at": "2026-01-02T01:00:00+08:00",
            }
            for day in dates
        ]
        subsidy_device_days.ingest(db, "test", station.operator_id, station, facts, is_demo=True)
    client = TestClient(app, client=("annual-device-" + secrets.token_hex(6), 1))
    try:
        login = client.post(
            "/api/v1/auth/login", json={"username": accounts[0][0], "password": accounts[1]}
        )
        assert login.status_code == 200
        import uuid

        payload = {
            "request_key": str(uuid.uuid4()),
            "application_year": 2025,
            "station_ids": [data["station"]],
            "policy_id": data["policy"],
            "application_note": "closed annual device evidence",
            "mode": "draft",
        }
        response = client.post(
            "/api/v1/subsidy/annual-batches",
            headers={"x-csrf-token": login.json()["csrf_token"]},
            data={"payload": json.dumps(payload)},
        )
        assert response.status_code == 201, response.text
        receipt = response.json()["receipt"]
        cid = receipt["items"][0]["id"]
        detail = client.get(f"/api/v1/subsidy/claims/{cid}").json()
        assert (
            detail["base_amount"] == "36.50"
            and detail["adjusted_amount"] == "43.80"
            and detail["total_kwh"] == "365.000000"
        )
        assert (
            detail["snapshot"]["device_calculation"]["row_count"] == 365
            and detail["snapshot"]["application_year"] == 2025
        )
        assert (
            client.get(
                f"/api/v1/subsidy/claims/{cid}/device-lines", params={"page_size": 100, "page": 4}
            ).json()["total"]
            == 365
        )
        replay = client.post(
            "/api/v1/subsidy/annual-batches",
            headers={"x-csrf-token": login.json()["csrf_token"]},
            data={"payload": json.dumps(payload)},
        )
        assert replay.status_code == 201 and replay.json() == {"replayed": True, "receipt": receipt}
    finally:
        client.close()


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity", "1E100", "-0.01"])
def test_invalid_or_unrepresentable_money_is_rejected(raw):
    from app.subsidy_device_execution import money

    with pytest.raises(HTTPException) as error:
        money(Decimal(raw))
    assert error.value.status_code == 422


def test_equal_decimal_policy_representations_do_not_block_frozen_claim(data):
    from app.subsidy_device_execution import verify

    with SessionLocal.begin() as db:
        claim = create(db, data)
        frozen = copy.deepcopy(claim.snapshot)
        frozen["policy_rate"] = "0.1000010"
        claim.snapshot = frozen
        policy = db.get(SubsidyPolicy, data["policy"])
        changed = copy.deepcopy(policy.device_rules)
        changed["swap_rate"] = "0.2000010"
        policy.device_rules = changed
        verify(db, claim, db.get(Station, data["station"]))


def test_frozen_coefficient_tampering_is_rejected_without_rewriting_claim(data):
    from app.subsidy_device_execution import verify

    with SessionLocal.begin() as db:
        claim = create(db, data)
        frozen = copy.deepcopy(claim.snapshot)
        frozen["device_calculation"]["star_coefficient"] = "1.000000"
        claim.snapshot = frozen
        with pytest.raises(HTTPException) as error:
            verify(db, claim, db.get(Station, data["station"]))
        assert error.value.status_code == 409
        assert claim.adjusted_amount == Decimal("5.05")


def test_subsidy_statistics_whole_period_frozen_rows_and_unallocated_adjustments(data, accounts):
    from app.subsidy_statistics import collect

    with SessionLocal.begin() as db:
        claim = create(db, data)
        claim.status = "approved"
        claim.adjusted_amount = Decimal("5.06")
    with SessionLocal() as db:
        user = db.get(User, data["user"])
        for dimension in ["operator", "station", "equipment", "device_day"]:
            rows, totals = collect(
                db, user, date(2026, 11, 1), date(2026, 11, 2), dimension, "accepted"
            )
            assert totals.claim_count == 1 and Decimal(totals.adjusted_amount) == Decimal("5.06")
            assert Decimal(totals.adjustment_amount) == Decimal(".01")
            assert sum(Decimal(row.base_amount) for row in rows) == Decimal("4.21")
            if dimension in {"equipment", "device_day"}:
                assert all(
                    row.adjusted_amount is None and row.initial_amount is None for row in rows
                )
                assert sum(row.device_rows for row in rows) == 2
            else:
                assert Decimal(rows[0].initial_amount) == Decimal("5.05")
        rows, totals = collect(
            db, user, date(2026, 11, 2), date(2026, 11, 2), "station", "accepted"
        )
        assert not rows and totals.claim_count == 0  # no partial financial proration
    with TestClient(app, client=("stats-" + secrets.token_hex(6), 1)) as client:
        assert (
            client.post(
                "/api/v1/auth/login", json={"username": accounts[0][0], "password": accounts[1]}
            ).status_code
            == 200
        )
        path = "/api/v1/subsidy/statistics?start_date=2026-11-01&end_date=2026-11-02&dimension=device_day"
        response = client.get(path)
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 2 and response.headers["cache-control"] == "no-store"
        csv = client.get(path.replace("statistics?", "statistics/export?"))
        assert csv.status_code == 200, csv.text
        assert csv.content.startswith(b"\xef\xbb\xbf") and "browser-device" not in csv.text
        options = client.get("/api/v1/subsidy/statistics/operators")
        assert options.status_code == 200 and options.headers["cache-control"] == "no-store"
        assert len(options.json()["items"]) == 1
        assert client.get(path + "&page_size=101").status_code == 422
        assert client.get(path.replace("2026-11-02", "2026-10-01")).status_code == 422


def test_subsidy_statistics_rejects_missing_lines_and_does_not_read_current_energy(data):
    from app.subsidy_statistics import collect

    with SessionLocal.begin() as db:
        claim = create(db, data)
        claim.status = "approved"
        cid = claim.id
        facts = list(
            db.scalars(
                select(SubsidyDeviceDay).where(SubsidyDeviceDay.station_id == data["station"])
            )
        )
        for fact in facts:
            fact.energy_kwh = Decimal(999)
    with SessionLocal() as db:
        rows, _ = collect(
            db,
            db.get(User, data["user"]),
            date(2026, 11, 1),
            date(2026, 11, 2),
            "equipment",
            "accepted",
        )
        assert Decimal(rows[0].eligible_kwh) == Decimal("42.125")
    with SessionLocal.begin() as db:
        db.execute(delete(SubsidyDeviceAllocation).where(SubsidyDeviceAllocation.claim_id == cid))
        line = db.scalar(select(SubsidyDeviceLine).where(SubsidyDeviceLine.claim_id == cid))
        db.delete(line)
    with SessionLocal() as db, pytest.raises(HTTPException) as error:
        collect(
            db,
            db.get(User, data["user"]),
            date(2026, 11, 1),
            date(2026, 11, 2),
            "equipment",
            "accepted",
        )
    assert error.value.status_code == 409


def test_subsidy_statistics_scope_limits_and_failed_export_has_no_success_audit(
    data, accounts, monkeypatch
):
    from app import subsidy_statistics as statistics

    with SessionLocal.begin() as db:
        claim = create(db, data)
        claim.status = "approved"
    with SessionLocal() as db:
        user = db.get(User, data["user"])
        user.allowed_districts = []
        rows, total = statistics.collect(
            db, user, date(2026, 11, 1), date(2026, 11, 2), "operator", "accepted"
        )
        assert not rows and total.claim_count == 0
    with TestClient(app, client=("stats-limit-" + secrets.token_hex(6), 1)) as client:
        assert (
            client.post(
                "/api/v1/auth/login", json={"username": accounts[0][1], "password": accounts[1]}
            ).status_code
            == 200
        )
        path = "/api/v1/subsidy/statistics?start_date=2026-11-01&end_date=2026-11-02"
        assert client.get(path + f"&station_id={data['station']}").json()["total"] == 0
    with TestClient(app, client=("stats-own-" + secrets.token_hex(6), 1)) as client:
        assert (
            client.post(
                "/api/v1/auth/login", json={"username": accounts[0][0], "password": accounts[1]}
            ).status_code
            == 200
        )
        monkeypatch.setattr(statistics, "CLAIM_LIMIT", 0)
        failed = client.get(path.replace("statistics?", "statistics/export?"))
        assert failed.status_code == 422
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(Audit)
                .where(Audit.user_id == data["user"], Audit.action == "subsidy.statistics.export")
            )
            == 0
        )


def test_subsidy_statistics_repeatable_read_and_changed_frozen_amount_rejected(data):
    from app.reporting import snapshot_session
    from app.subsidy_statistics import collect

    with SessionLocal.begin() as db:
        claim = create(db, data)
        claim.status = "approved"
        cid = claim.id
    read_session = snapshot_session()
    db = next(read_session)
    try:
        db.scalar(select(SubsidyClaim.id).where(SubsidyClaim.id == cid))
        with SessionLocal.begin() as writer:
            line = writer.scalar(
                select(SubsidyDeviceLine)
                .where(SubsidyDeviceLine.claim_id == cid)
                .order_by(SubsidyDeviceLine.fact_id)
            )
            value = copy.deepcopy(line.snapshot)
            value["amount"] = "999.00"
            line.snapshot = value
        rows, _ = collect(
            db,
            db.get(User, data["user"]),
            date(2026, 11, 1),
            date(2026, 11, 2),
            "equipment",
            "accepted",
        )
        assert Decimal(rows[0].base_amount) == Decimal("4.21")
    finally:
        read_session.close()
    with SessionLocal() as db, pytest.raises(HTTPException) as error:
        collect(
            db,
            db.get(User, data["user"]),
            date(2026, 11, 1),
            date(2026, 11, 2),
            "equipment",
            "accepted",
        )
    assert error.value.status_code == 409
