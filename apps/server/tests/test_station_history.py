import secrets
from contextlib import ExitStack
from datetime import UTC, date, datetime, timedelta
from decimal import localcontext

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, select

from app.db import SessionLocal, engine
from app.main import app
from app.models import ApprovalAction, EnergyRecord, RoleProfile, Station, StationApplication, User
from app.sync import ingest


@pytest.fixture()
def history(accounts):
    yield
    with SessionLocal.begin() as db:
        db.execute(delete(EnergyRecord).where(EnergyRecord.station_id.in_(accounts[2])))
        db.execute(
            delete(ApprovalAction).where(
                ApprovalAction.application_id.in_(
                    select(StationApplication.id).where(
                        StationApplication.station_id.in_(accounts[2])
                    )
                )
            )
        )


def login(stack, name, password):
    client = stack.enter_context(
        TestClient(app, client=("station-history-" + secrets.token_hex(6), 1))
    )
    assert (
        client.post("/api/v1/auth/login", json={"username": name, "password": password}).status_code
        == 200
    )
    return client


def add_energy(db, sid, source, day, kwh, eligible=True):
    db.add(
        EnergyRecord(
            station_id=sid,
            source=source,
            external_id=secrets.token_hex(12),
            day=date.fromisoformat(day),
            kwh=kwh,
            eligible=eligible,
        )
    )


def test_station_energy_keeps_exact_sources_zero_missing_exclusions_and_bounds(
    accounts, staff, history
):
    names, password, ids = accounts
    with SessionLocal.begin() as db:
        add_energy(db, ids[0], "source-a", "2024-02-28", "0.1")
        add_energy(db, ids[0], "source-a", "2024-02-28", "0.2")
        add_energy(db, ids[0], "source-a", "2024-02-29", "0")
        add_energy(db, ids[0], "source-a", "2024-03-01", "999", False)
        add_energy(db, ids[0], "source-b", "2024-02-28", "999999999999.999999")
        add_energy(db, ids[0], "source-b", "2024-02-29", "0.1")
        add_energy(db, ids[0], "excluded-only", "2024-02-29", "8", False)
        add_energy(db, ids[1], "private-other", "2024-02-28", "999")
    with ExitStack() as stack, localcontext() as ctx:
        ctx.prec = 3  # SQL sums must remain exact independently of Python Decimal context.
        owner = login(stack, names[0], password)
        base = f"/api/v1/stations/{ids[0]}/energy-history"
        params = {"start_date": "2024-02-28", "end_date": "2024-03-01"}
        result = owner.get(base, params=params)
        assert result.status_code == 200
        data = result.json()
        streams = {s["source"]: s for s in data["streams"]}
        a = streams["source-a"]
        assert (
            a["kwh"] == "0.300000"
            and a["records"] == 3
            and a["observed_days"] == 2
            and a["missing_days"] == 1
        )
        assert a["excluded_records"] == 1 and a["extra_records_on_observed_days"] == 1
        assert a["days"][1]["day"] == "2024-02-29" and a["days"][1]["kwh"] == "0.000000"
        assert a["days"][2]["kwh"] is None and a["days"][2]["excluded_records"] == 1
        assert streams["source-b"]["kwh"] == "1000000000000.099999"
        assert streams["excluded-only"]["kwh"] is None and streams["excluded-only"]["records"] == 0
        assert "private-other" not in streams and data["source_completeness_verified"] is False
        assert owner.get(base, params={**params, "source": "source-a"}).json()["streams"] == [a]
        assert owner.get(base, params={**params, "source": "absent"}).json()["streams"] == []
        assert (
            owner.get(
                base, params={"start_date": "2024-01-01", "end_date": "2024-12-31"}
            ).status_code
            == 200
        )
        assert (
            owner.get(
                base, params={"start_date": "2024-01-01", "end_date": "2025-01-01"}
            ).status_code
            == 422
        )
        assert owner.get(base, params={**params, "start_date": "2024-03-02"}).status_code == 422
        assert (
            owner.get(
                base, params={"start_date": "1899-01-01", "end_date": "1899-01-02"}
            ).status_code
            == 422
        )
        other = login(stack, names[1], password)
        assoc = login(stack, staff[0][1], password)
        reg = login(stack, staff[0][0], password)
        assert other.get(base, params=params).status_code == 404
        assert assoc.get(base, params=params).json()["streams"] == data["streams"]
        with SessionLocal.begin() as db:
            db.scalar(select(User).where(User.username == staff[0][0])).allowed_districts = []
        assert reg.get(base, params=params).status_code == 404


def test_energy_source_bound_requires_exact_filter(accounts, history):
    names, password, ids = accounts
    with SessionLocal.begin() as db:
        for index in range(21):
            add_energy(db, ids[0], f"s-{index}", "2026-11-01", "1")
    with ExitStack() as stack:
        client = login(stack, names[0], password)
        base = f"/api/v1/stations/{ids[0]}/energy-history"
        params = {"start_date": "2026-11-01", "end_date": "2026-11-01"}
        assert client.get(base, params=params).status_code == 422
        assert len(client.get(base, params={**params, "source": "s-20"}).json()["streams"]) == 1


def test_flow_reads_actual_actions_and_private_scopes_with_both_pages(accounts, staff, history):
    names, password, ids = accounts
    stamp = datetime(2026, 11, 1, 0, tzinfo=UTC)
    with SessionLocal.begin() as db:
        own = db.scalar(select(User).where(User.username == names[0]))
        regulator = db.scalar(select(User).where(User.username == staff[0][0]))
        s = db.get(Station, ids[0])
        s.status = "operating"
        s.source_first_connected_at = stamp
        s.source_first_reported_at = stamp + timedelta(minutes=5)
        appids = []
        for index in range(23):
            a = StationApplication(
                station_id=s.id,
                operator_id=own.operator_id,
                applicant_id=own.id,
                kind="access",
                status="approved",
                payload={},
            )
            db.add(a)
            db.flush()
            appids.append(a.id)
        # A historical approved row with no action must not manufacture approval proof.
        for index in range(23):
            action = "approve" if index == 22 else "submit"
            db.add(
                ApprovalAction(
                    application_id=appids[0],
                    actor_id=regulator.id if action == "approve" else own.id,
                    action=action,
                    comment=f"proof-{index}",
                    created_at=stamp + timedelta(minutes=index),
                )
            )
        other_user = db.scalar(select(User).where(User.username == names[1]))
        other = StationApplication(
            station_id=ids[1],
            operator_id=other_user.operator_id,
            applicant_id=other_user.id,
            kind="access",
            status="submitted",
            payload={},
        )
        db.add(other)
        db.flush()
        other_id = other.id
    with ExitStack() as stack:
        client = login(stack, names[0], password)
        association = login(stack, staff[0][1], password)
        outside = login(stack, names[1], password)
        base = f"/api/v1/stations/{ids[0]}/flow"
        data = client.get(base, params={"page": 3, "page_size": 10}).json()
        assert data["total"] == 23 and len(data["items"]) == 3 and data["page"] == 3
        assert data["platform_status"] == "operating" and data["reporting_state"] is None
        assert datetime.fromisoformat(data["source_first_reported_at"]) == stamp + timedelta(
            minutes=5
        )
        assert datetime.fromisoformat(data["last_access_approved_at"]) == stamp + timedelta(
            minutes=22
        )
        assert data["items"][-1]["action_count"] == 23
        first = client.get(base).json()["items"][0]
        assert first["last_submitted_at"] is None and first["last_approved_at"] is None
        path = f"{base}/{appids[0]}/actions"
        actions = client.get(path, params={"page": 2, "page_size": 10}).json()
        assert (
            actions["total"] == 23
            and len(actions["items"]) == 10
            and actions["items"][0]["comment"] == "proof-12"
        )
        assert client.get(f"{base}/{other_id}/actions").status_code == 404
        assert outside.get(base).status_code == outside.get(path).status_code == 404
        assert association.get(base).status_code == association.get(path).status_code == 403
        assert client.get(base + "?page_size=101").status_code == 422
        assert client.get(path + "?page=2147483648").status_code == 422


def test_station_only_profile_gains_no_statistics_or_application_access(accounts, history):
    names, password, ids = accounts
    profile_id = None
    try:
        with SessionLocal.begin() as db:
            p = RoleProfile(
                name="history-" + secrets.token_hex(8),
                base_role="operator",
                permissions=["stations.read"],
            )
            db.add(p)
            db.flush()
            profile_id = p.id
            db.scalar(select(User).where(User.username == names[0])).role_profile_id = p.id
        with ExitStack() as stack:
            c = login(stack, names[0], password)
            assert c.get(f"/api/v1/stations/{ids[0]}/flow").status_code == 403
            assert (
                c.get(
                    f"/api/v1/stations/{ids[0]}/energy-history?start_date=2026-11-01&end_date=2026-11-01"
                ).status_code
                == 403
            )
            assert c.get(f"/api/v1/stations/{ids[0]}").status_code == 200
    finally:
        with SessionLocal.begin() as db:
            db.scalar(select(User).where(User.username == names[0])).role_profile_id = None
            db.flush()
            if profile_id:
                db.execute(delete(RoleProfile).where(RoleProfile.id == profile_id))


def test_energy_history_same_snapshot_through_concurrent_source_and_station_updates(
    accounts, history
):
    names, password, ids = accounts
    with SessionLocal.begin() as db:
        add_energy(db, ids[0], "old", "2026-11-01", "0.1")
    fired = False

    def commit_after_sources(connection, cursor, statement, parameters, context, many):
        nonlocal fired
        if fired or "count(distinct(energy_records.source))" not in statement.lower():
            return
        fired = True
        with SessionLocal.begin() as db:
            db.get(Station, ids[0]).name = "new-name"
            add_energy(db, ids[0], "new", "2026-11-01", "99")

    with ExitStack() as stack:
        c = login(stack, names[0], password)
        path = f"/api/v1/stations/{ids[0]}/energy-history?start_date=2026-11-01&end_date=2026-11-01"
        event.listen(engine, "after_cursor_execute", commit_after_sources)
        try:
            first = c.get(path).json()
        finally:
            event.remove(engine, "after_cursor_execute", commit_after_sources)
        assert fired and first["station_name"] == "隔离测试" and len(first["streams"]) == 1
        second = c.get(path).json()
        assert second["station_name"] == "new-name" and len(second["streams"]) == 2


@pytest.mark.parametrize("stamp", ["2026-11-01T08:05:00+08:00", None, "omitted"])
def test_source_first_report_is_independent_preservable_and_clearable(accounts, history, stamp):
    with SessionLocal.begin() as db:
        s = db.get(Station, accounts[2][0])
        s.source_first_reported_at = datetime(2025, 1, 1, tzinfo=UTC)
        body = {
            "external_id": s.external_id,
            "name": s.name,
            "district": s.district,
            "address": s.address,
            "power_kw": "120",
            "equipment": [],
        }
        if stamp != "omitted":
            body["source_first_reported_at"] = stamp
        ingest(db, s.source, s.operator_id, [body])
    with SessionLocal() as db:
        s = db.get(Station, accounts[2][0])
        assert s.source_first_connected_at is None
        expected = (
            datetime.fromisoformat(stamp)
            if stamp and stamp != "omitted"
            else datetime(2025, 1, 1, tzinfo=UTC)
            if stamp == "omitted"
            else None
        )
        assert s.source_first_reported_at == expected


def test_naive_source_first_report_rolls_back(accounts, history):
    with pytest.raises(ValueError), SessionLocal.begin() as db:
        s = db.get(Station, accounts[2][0])
        ingest(
            db,
            s.source,
            s.operator_id,
            [
                {
                    "external_id": s.external_id,
                    "name": "must rollback",
                    "district": s.district,
                    "address": s.address,
                    "power_kw": "120",
                    "equipment": [],
                    "source_first_reported_at": "2026-11-01T08:00:00",
                }
            ],
        )
    with SessionLocal() as db:
        assert db.get(Station, accounts[2][0]).name == "隔离测试"


def test_flow_snapshot_keeps_platform_state_counts_and_action_proof_together(accounts, history):
    names, password, ids = accounts
    with SessionLocal.begin() as db:
        u = db.scalar(select(User).where(User.username == names[0]))
        uid = u.id
        oid = u.operator_id
        a = StationApplication(
            station_id=ids[0],
            operator_id=oid,
            applicant_id=uid,
            kind="access",
            status="submitted",
            payload={},
        )
        db.add(a)
        db.flush()
        aid = a.id
        db.add(ApprovalAction(application_id=aid, actor_id=uid, action="submit"))
    fired = False

    def commit_after_total(connection, cursor, statement, parameters, context, many):
        nonlocal fired
        if (
            fired
            or "count(*)" not in statement.lower()
            or "station_applications" not in statement.lower()
        ):
            return
        fired = True
        with SessionLocal.begin() as db:
            db.get(Station, ids[0]).status = "access_approved"
            db.get(StationApplication, aid).status = "approved"
            db.add(
                ApprovalAction(
                    application_id=aid,
                    actor_id=uid,
                    action="approve",
                    comment="Concurrent fixture proof",
                )
            )
            db.add(
                StationApplication(
                    station_id=ids[0],
                    operator_id=oid,
                    applicant_id=uid,
                    kind="change",
                    status="withdrawn",
                    payload={},
                )
            )

    with ExitStack() as stack:
        c = login(stack, names[0], password)
        path = f"/api/v1/stations/{ids[0]}/flow"
        event.listen(engine, "after_cursor_execute", commit_after_total)
        try:
            first = c.get(path).json()
        finally:
            event.remove(engine, "after_cursor_execute", commit_after_total)
        assert fired and first["total"] == 1 and first["platform_status"] == "pending"
        assert first["last_access_approved_at"] is None and first["items"][0]["action_count"] == 1
        second = c.get(path).json()
        assert (
            second["total"] == 2
            and second["platform_status"] == "access_approved"
            and second["last_access_approved_at"] is not None
        )
