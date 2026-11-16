import csv
import io
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import faults
from app.db import SessionLocal
from app.main import app
from app.models import FaultEvent, Station, User

STAMP = datetime(2026, 11, 1, tzinfo=UTC)
BASE = "/api/v1/statistics/faults"


def sample(**patch):
    return {
        "external_id": "event-1",
        "equipment_external_id": "equipment-raw",
        "connector_external_id": "connector-raw",
        "fault_code": "F-DEMO",
        "status": "active",
        "severity": "warning",
        "started_at": STAMP,
        "source_updated_at": STAMP + timedelta(hours=3),
        **patch,
    }


def test_fault_revision_ownership_exact_lifecycle_atomic_batch_and_concurrency(accounts):
    sid = accounts[2][0]

    def import_one():
        with SessionLocal.begin() as db:
            station = db.get(Station, sid)
            return faults.ingest(
                db, "test", station.operator_id, station, [sample()], is_demo=True
            )["changed"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: import_one(), range(2))) == [0, 1]
    with SessionLocal.begin() as db:
        station = db.get(Station, sid)
        assert (
            faults.ingest(
                db,
                "test",
                station.operator_id,
                station,
                [sample(source_updated_at=STAMP)],
                is_demo=True,
            )["ignored_stale"]
            == 1
        )
        with pytest.raises(ValueError, match="conflicting"):
            faults.ingest(
                db,
                "test",
                station.operator_id,
                station,
                [sample(severity="critical")],
                is_demo=True,
            )
        faults.ingest(
            db,
            "test",
            station.operator_id,
            station,
            [
                sample(
                    status="recovered",
                    recovered_at=STAMP + timedelta(microseconds=1),
                    source_updated_at=STAMP + timedelta(hours=4),
                )
            ],
            is_demo=True,
        )
        row = db.scalar(select(FaultEvent).where(FaultEvent.station_id == sid))
        assert row.version == 2 and row.recovered_at - row.started_at == timedelta(microseconds=1)
        with pytest.raises(ValueError, match="ownership"):
            faults.ingest(db, "wrong", station.operator_id, station, [sample()], is_demo=True)
        for patch in [
            {"started_at": datetime(2026, 11, 1)},
            {"source_updated_at": datetime(2026, 11, 1)},
            {"status": "recovered"},
            {"recovered_at": STAMP},
            {"status": "recovered", "recovered_at": STAMP - timedelta(seconds=1)},
            {"equipment_external_id": None},
            {"external_id": " "},
            {"phone": "private"},
            {"severity": "bogus"},
        ]:
            with pytest.raises(ValidationError):
                faults.SourceFaultEvent.model_validate(sample(**patch))
        with pytest.raises(IntegrityError), db.begin_nested():
            row.recovered_at = None
            db.flush()
        db.refresh(row)
    with SessionLocal() as db:
        station = db.get(Station, sid)
        with pytest.raises(ValidationError):
            faults.ingest(
                db,
                "test",
                station.operator_id,
                station,
                [sample(external_id="atomic-new"), sample(external_id="bad", status="recovered")],
                is_demo=True,
            )
        db.rollback()
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(FaultEvent.id).where(
                    FaultEvent.external_id == "atomic-new", FaultEvent.station_id == sid
                )
            )
            is None
        )


def test_fault_pagination_literal_filters_coverage_csv_and_identity(accounts, staff):
    names, password, sids = accounts
    with SessionLocal.begin() as db:
        for index, sid in enumerate(sids):
            station = db.get(Station, sid)
            station.name = "=SUM(1,2)" if index == 0 else "foreign"
            data = (
                [
                    sample(external_id=f"event-{i:03}", started_at=STAMP + timedelta(seconds=i))
                    for i in range(122)
                ]
                if index == 0
                else [sample()]
            )
            data += (
                [
                    sample(external_id="literal%_", status="recovered", recovered_at=STAMP),
                    sample(
                        external_id="recovered-2",
                        status="recovered",
                        recovered_at=STAMP + timedelta(microseconds=2),
                    ),
                    sample(external_id="unknown", status="unknown"),
                ]
                if index == 0
                else []
            )
            faults.ingest(db, "test", station.operator_id, station, data, is_demo=True)
        station = db.get(Station, sids[0])
        faults.ingest(
            db,
            "test",
            station.operator_id,
            station,
            [
                sample(
                    external_id="real-isolated",
                    status="recovered",
                    recovered_at=STAMP + timedelta(seconds=99),
                )
            ],
            is_demo=False,
        )
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/v1/auth/login", json={"username": names[0], "password": password}
            ).status_code
            == 200
        )
        listing = client.get(BASE, params={"page": 7}).json()
        assert listing["total"] == 126 and len(listing["items"]) == 6
        assert listing["source_completeness_verified"] is False
        filtered = client.get(BASE, params={"q": "%_"}).json()
        assert filtered["total"] == 1 and filtered["items"][0]["recovery_elapsed_seconds"] == "0"
        streams = client.get(BASE + "/summary").json()["streams"]
        demo = next(s for s in streams if s["is_demo"])
        assert (
            demo["observed_events"],
            demo["active_events"],
            demo["recovered_events"],
            demo["unknown_events"],
        ) == (125, 122, 2, 1)
        assert Decimal(demo["average_recovery_elapsed_seconds"]) == Decimal("0.000001")
        assert (
            Decimal(
                next(s for s in streams if not s["is_demo"])["average_recovery_elapsed_seconds"]
            )
            == 99
        )
        assert (
            client.get(BASE + "/summary", params={"status": "active"}).json()["streams"][0][
                "average_recovery_elapsed_seconds"
            ]
            is None
        )
        assert client.get(BASE, params={"station_id": sids[1]}).json()["total"] == 0
        assert (
            client.get(
                BASE, params={"connector_external_id": "connector-raw", "status": "recovered"}
            ).json()["total"]
            == 3
        )
        assert (
            client.get(
                BASE,
                params={
                    "start_at": STAMP.isoformat(),
                    "end_at": (STAMP + timedelta(seconds=1)).isoformat(),
                    "status": "active",
                },
            ).json()["total"]
            == 1
        )
        for params in [
            {"page_size": 101},
            {"status": "confirmed"},
            {"start_at": "2026-11-01T00:00:00"},
            {"start_at": STAMP.isoformat(), "end_at": STAMP.isoformat()},
        ]:
            assert client.get(BASE, params=params).status_code == 422
        exported = client.get(
            BASE + "/export",
            params={
                "start_at": STAMP.isoformat(),
                "end_at": (STAMP + timedelta(days=1)).isoformat(),
            },
        )
        assert exported.status_code == 200
        rows = list(csv.reader(io.StringIO(exported.text.lstrip("\ufeff"))))
        assert len(rows) == 127 and all(row[3].startswith("'=SUM") for row in rows[1:])
        assert client.get(BASE + "/summary", params={"q": "nothing"}).json()["streams"] == []
    for username, expected in [(staff[0][0], 127), (staff[0][1], None)]:
        with TestClient(app) as client:
            assert (
                client.post(
                    "/api/v1/auth/login", json={"username": username, "password": password}
                ).status_code
                == 200
            )
            response = client.get(BASE)
            if expected is None:
                assert response.status_code == 403
                assert client.get(BASE + "/summary").status_code == 403
            else:
                # The regulator may also see persistent development demo records.
                # Prove both fixture enterprises are visible without counting unrelated data.
                assert client.get(BASE, params={"station_id": sids[0]}).json()["total"] == 126
                assert client.get(BASE, params={"station_id": sids[1]}).json()["total"] == 1
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.username == names[0]))
        user.allowed_districts = []
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/v1/auth/login", json={"username": names[0], "password": password}
            ).status_code
            == 200
        )
        assert client.get(BASE).json()["total"] == 0
        assert client.get(BASE + "/summary").json()["streams"] == []


def test_fault_export_limit_month_boundary_and_readonly_grant(accounts):
    from sqlalchemy import delete, insert

    from app.models import Audit, RoleProfile

    names, password, sids = accounts
    profile_id = None
    with SessionLocal.begin() as db:
        station = db.get(Station, sids[0])
        uid = db.scalar(select(User.id).where(User.username == names[0]))
        db.execute(
            insert(FaultEvent),
            [
                {
                    "station_id": station.id,
                    "operator_id": station.operator_id,
                    "source": "test",
                    "external_id": f"limit-{i}",
                    "status": "active",
                    "severity": "unknown",
                    "started_at": STAMP,
                    "source_updated_at": STAMP,
                    "is_demo": True,
                }
                for i in range(10001)
            ],
        )
    window = {"start_at": "2026-10-01T00:00:00+08:00", "end_at": "2026-12-01T00:00:00+08:00"}
    try:
        with TestClient(app, client=("fault-export-" + secrets.token_hex(8), 1)) as client:
            assert (
                client.post(
                    "/api/v1/auth/login", json={"username": names[0], "password": password}
                ).status_code
                == 200
            )
            assert client.get(BASE + "/export").status_code == 422
            assert (
                client.get(
                    BASE + "/export",
                    params={
                        **window,
                        "q": "limit-10000",
                        "end_at": "2026-12-01T00:00:00.000001+08:00",
                    },
                ).status_code
                == 422
            )
            assert client.get(BASE + "/export", params=window).status_code == 422
            with SessionLocal() as db:
                assert (
                    db.scalar(
                        select(Audit.id).where(
                            Audit.user_id == uid, Audit.action == "statistics.faults.export"
                        )
                    )
                    is None
                )
            assert (
                client.get(BASE + "/export", params={**window, "q": "limit-10000"}).status_code
                == 200
            )
            with SessionLocal.begin() as db:
                profile = RoleProfile(
                    name="fault-read-" + secrets.token_hex(8),
                    base_role="operator",
                    permissions=["statistics.read"],
                )
                db.add(profile)
                db.flush()
                profile_id = profile.id
                db.get(User, uid).role_profile_id = profile_id
            assert client.get(BASE, params={"q": "limit-10000"}).json()["total"] == 1
            assert client.get(BASE + "/summary", params={"q": "limit-10000"}).status_code == 200
            assert (
                client.get(BASE + "/export", params={**window, "q": "limit-10000"}).status_code
                == 403
            )
    finally:
        with SessionLocal.begin() as db:
            db.get(User, uid).role_profile_id = None
            db.flush()
            if profile_id:
                db.execute(delete(RoleProfile).where(RoleProfile.id == profile_id))
