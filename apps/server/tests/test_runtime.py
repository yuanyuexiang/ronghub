import csv
import io
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, insert, select, text
from sqlalchemy.exc import IntegrityError

from app import runtime
from app.db import SessionLocal
from app.main import app
from app.models import Audit, RoleProfile, RuntimeInterval, Station, User

STAMP = datetime(2026, 11, 1, tzinfo=UTC)
BASE = "/api/v1/statistics/runtime"


def sample(**patch):
    return {
        "external_id": "interval-1",
        "equipment_external_id": "%_device",
        "connector_external_id": "C1",
        "dimension": "connectivity",
        "state": "online",
        "started_at": STAMP,
        "ended_at": STAMP + timedelta(hours=1),
        "source_updated_at": STAMP + timedelta(days=1),
        **patch,
    }


def ingest_one(sid, records, demo=True):
    with SessionLocal.begin() as db:
        station = db.get(Station, sid)
        return runtime.ingest(db, "test", station.operator_id, station, records, is_demo=demo)


def test_runtime_source_revision_batch_boundary_correction_and_database_exclusion(accounts):
    sid = accounts[2][0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: ingest_one(sid, [sample()])["changed"], range(2))) == [
            0,
            1,
        ]
    ingest_one(
        sid,
        [
            sample(
                external_id="next",
                state="offline",
                started_at=STAMP + timedelta(hours=1),
                ended_at=STAMP + timedelta(hours=2),
            )
        ],
    )
    # Expanding the first row temporarily overlaps its neighbor; the final batch is coherent.
    revision = STAMP + timedelta(days=2)
    assert (
        ingest_one(
            sid,
            [
                sample(ended_at=STAMP + timedelta(minutes=90), source_updated_at=revision),
                sample(
                    external_id="next",
                    state="offline",
                    started_at=STAMP + timedelta(minutes=90),
                    ended_at=STAMP + timedelta(hours=2),
                    source_updated_at=revision,
                ),
            ],
        )["changed"]
        == 2
    )
    assert ingest_one(sid, [sample()])["ignored_stale"] == 1
    with pytest.raises(ValueError, match="conflicting"):
        ingest_one(
            sid,
            [
                sample(
                    ended_at=STAMP + timedelta(minutes=90),
                    state="offline",
                    source_updated_at=revision,
                )
            ],
        )
    with pytest.raises(ValueError, match="objects"):
        ingest_one(sid, [sample(connector_external_id="C2")])
    with pytest.raises(IntegrityError):
        ingest_one(
            sid,
            [
                sample(
                    external_id="batch-ok",
                    started_at=STAMP + timedelta(hours=3),
                    ended_at=STAMP + timedelta(hours=4),
                ),
                sample(external_id="batch-bad"),
            ],
        )
    with SessionLocal.begin() as db:
        station = db.get(Station, sid)
        assert (
            db.scalar(
                select(RuntimeInterval.id).where(
                    RuntimeInterval.station_id == sid, RuntimeInterval.external_id == "batch-ok"
                )
            )
            is None
        )
        # Direct SQL bypasses the adapter and still cannot introduce overlapping state time.
        with pytest.raises(IntegrityError), db.begin_nested():
            db.execute(
                insert(RuntimeInterval).values(
                    station_id=sid,
                    operator_id=station.operator_id,
                    source="test",
                    is_demo=True,
                    **sample(external_id="direct-overlap"),
                )
            )
        constraint = db.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'runtime_intervals'::regclass AND conname = 'ex_runtime_no_overlap'"
            )
        )
        assert (
            "EXCLUDE USING gist" in constraint
            and "tstzrange" in constraint
            and "DEFERRABLE" in constraint
        )
        for patch in [
            {"started_at": datetime(2026, 11, 1)},
            {"ended_at": STAMP},
            {"ended_at": None},
            {"source_updated_at": STAMP},
            {"state": "faulted"},
            {"dimension": "operation", "state": "offline"},
            {"equipment_external_id": " "},
            {"connector_external_id": " "},
            {"raw_payload": {"phone": "private"}},
        ]:
            with pytest.raises(ValidationError):
                runtime.SourceRuntimeInterval.model_validate(sample(**patch))
        with pytest.raises(ValueError, match="ownership"):
            runtime.ingest(
                db, "other-source", station.operator_id, station, [sample()], is_demo=True
            )


def test_runtime_concurrent_conflicting_intervals_have_one_commit(accounts):
    sid = accounts[2][0]

    def run(index):
        try:
            ingest_one(
                sid,
                [
                    sample(
                        external_id=f"conflict-{index}", state="online" if index == 0 else "offline"
                    )
                ],
            )
            return "committed"
        except IntegrityError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, range(2))) == ["committed", "rejected"]


def window(sid, **patch):
    return {
        "station_id": sid,
        "source": "test",
        "equipment_external_id": "%_device",
        "connector_external_id": "C1",
        "start_at": STAMP.isoformat(),
        "end_at": (STAMP + timedelta(hours=4)).isoformat(),
        **patch,
    }


def test_runtime_clipping_axes_unknown_unreported_demo_csv_and_scope(accounts, staff):
    names, password, sids = accounts
    sid = sids[0]
    ingest_one(
        sid,
        [
            sample(started_at=STAMP - timedelta(hours=1)),
            sample(
                external_id="offline",
                state="offline",
                started_at=STAMP + timedelta(hours=1),
                ended_at=STAMP + timedelta(hours=2),
            ),
            sample(
                external_id="unknown",
                state="unknown",
                started_at=STAMP + timedelta(hours=2),
                ended_at=STAMP + timedelta(hours=3),
            ),
            sample(external_id="faulted", dimension="operation", state="faulted"),
            sample(
                external_id="maintenance",
                dimension="operation",
                state="maintenance",
                started_at=STAMP + timedelta(hours=1),
                ended_at=STAMP + timedelta(hours=2),
            ),
            sample(
                external_id="equipment-level",
                connector_external_id="",
                ended_at=STAMP + timedelta(hours=4),
            ),
        ],
    )
    ingest_one(
        sid, [sample(external_id="non-demo", ended_at=STAMP + timedelta(hours=4))], demo=False
    )
    ingest_one(sids[1], [sample()])
    with SessionLocal.begin() as db:
        db.get(Station, sid).name = "=SUM(1,2)"
    with TestClient(app) as op:
        assert (
            op.post(
                "/api/v1/auth/login", json={"username": names[0], "password": password}
            ).status_code
            == 200
        )
        response = op.get(BASE, params={**window(sid), "page_size": 2})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["total"] == 6 and len(data["items"]) == 2 and data["window_seconds"] == "14400"
        demo = next(stream for stream in data["streams"] if stream["is_demo"])
        conn = next(axis for axis in demo["axes"] if axis["dimension"] == "connectivity")
        assert tuple(
            Decimal(conn[key])
            for key in [
                "reported_seconds",
                "known_seconds",
                "unknown_seconds",
                "unreported_seconds",
            ]
        ) == (10800, 7200, 3600, 3600)
        assert [state["share_of_reported_pct"] for state in conn["states"]] == ["33.33"] * 3
        operation = next(axis for axis in demo["axes"] if axis["dimension"] == "operation")
        assert Decimal(operation["unreported_seconds"]) == 7200
        assert (
            Decimal(
                next(state for state in operation["states"] if state["state"] == "faulted")[
                    "observed_seconds"
                ]
            )
            == 3600
        )
        first = next(item for item in data["items"] if item["external_id"] == "interval-1")
        assert (
            datetime.fromisoformat(first["clipped_start_at"]) == STAMP
            and first["observed_seconds"] == "3600"
        )
        assert op.get(BASE, params=window(sids[1])).status_code == 404
        assert (
            op.get(BASE, params=window(sid, connector_external_id="missing")).json()["streams"]
            == []
        )
        equipment = op.get(BASE, params=window(sid, connector_external_id="")).json()
        assert (
            equipment["total"] == 1
            and Decimal(equipment["streams"][0]["axes"][0]["reported_seconds"]) == 14400
        )
        objects = op.get(
            BASE + "/objects", params={"station_id": sid, "source": "test", "q": "%_"}
        ).json()
        assert objects["total"] == 2
        exported = op.get(BASE + "/export", params=window(sid))
        assert exported.status_code == 200
        rows = list(csv.reader(io.StringIO(exported.text.lstrip("\ufeff"))))
        assert len(rows) == 7 and all(row[0].startswith("'=SUM") for row in rows[1:])
        for patch in [
            {"start_at": "2026-11-01T00:00:00"},
            {"end_at": STAMP.isoformat()},
            {"end_at": (STAMP + timedelta(days=367)).isoformat()},
            {"page_size": 101},
            {"equipment_external_id": " "},
        ]:
            assert op.get(BASE, params={**window(sid), **patch}).status_code == 422
    for username, code in [(staff[0][0], 200), (staff[0][1], 403)]:
        with TestClient(app) as client:
            assert (
                client.post(
                    "/api/v1/auth/login", json={"username": username, "password": password}
                ).status_code
                == 200
            )
            assert client.get(BASE, params=window(sids[1])).status_code == code
            assert client.get(BASE + "/stations").status_code == code
            assert (
                client.get(
                    BASE + "/objects", params={"station_id": sids[1], "source": "test"}
                ).status_code
                == code
            )
    with SessionLocal.begin() as db:
        db.scalar(select(User).where(User.username == names[0])).allowed_districts = []
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/v1/auth/login", json={"username": names[0], "password": password}
            ).status_code
            == 200
        )
        assert client.get(BASE, params=window(sid)).status_code == 404
        assert (
            client.get(BASE + "/objects", params={"station_id": sid, "source": "test"}).status_code
            == 404
        )


def test_runtime_history_object_paging_export_limit_and_readonly_grant(accounts):
    names, password, sids = accounts
    sid = sids[0]
    profile_id = None
    with SessionLocal.begin() as db:
        station = db.get(Station, sid)
        uid = db.scalar(select(User.id).where(User.username == names[0]))
        db.execute(
            insert(RuntimeInterval),
            [
                {
                    "station_id": sid,
                    "operator_id": station.operator_id,
                    "source": "test",
                    "is_demo": True,
                    **sample(
                        external_id=f"history-{index}",
                        started_at=STAMP + timedelta(seconds=index),
                        ended_at=STAMP + timedelta(seconds=index + 1),
                    ),
                }
                for index in range(10001)
            ],
        )
        db.execute(
            insert(RuntimeInterval),
            [
                {
                    "station_id": sid,
                    "operator_id": station.operator_id,
                    "source": "test",
                    "is_demo": True,
                    **sample(
                        external_id=f"object-{index}", equipment_external_id=f"object-{index:03}"
                    ),
                }
                for index in range(122)
            ],
        )
    try:
        with TestClient(app, client=("runtime-paging-" + secrets.token_hex(8), 1)) as client:
            assert (
                client.post(
                    "/api/v1/auth/login", json={"username": names[0], "password": password}
                ).status_code
                == 200
            )
            page = client.get(BASE, params={**window(sid), "page": 7}).json()
            assert (
                page["total"] == 10001
                and len(page["items"]) == 20
                and page["items"][0]["external_id"] == "history-120"
            )
            objects = client.get(
                BASE + "/objects", params={"station_id": sid, "source": "test", "page": 7}
            ).json()
            assert objects["total"] == 123 and len(objects["items"]) == 3
            assert client.get(BASE + "/export", params=window(sid)).status_code == 422
            with SessionLocal() as db:
                assert (
                    db.scalar(
                        select(Audit.id).where(
                            Audit.user_id == uid, Audit.action == "statistics.runtime.export"
                        )
                    )
                    is None
                )
            assert (
                client.get(
                    BASE + "/export",
                    params=window(sid, end_at=(STAMP + timedelta(seconds=1)).isoformat()),
                ).status_code
                == 200
            )
            with SessionLocal.begin() as db:
                profile = RoleProfile(
                    name="runtime-read-" + secrets.token_hex(8),
                    base_role="operator",
                    permissions=["statistics.read"],
                )
                db.add(profile)
                db.flush()
                profile_id = profile.id
                db.get(User, uid).role_profile_id = profile_id
            assert client.get(BASE, params=window(sid)).status_code == 200
            station_options = client.get(BASE + "/stations").json()
            assert station_options["total"] == 1 and station_options["items"][0]["id"] == sid
            assert (
                client.get(
                    BASE + "/objects", params={"station_id": sid, "source": "test"}
                ).status_code
                == 200
            )
            assert client.get(BASE + "/export", params=window(sid)).status_code == 403
    finally:
        with SessionLocal.begin() as db:
            db.get(User, uid).role_profile_id = None
            db.flush()
            if profile_id:
                db.execute(delete(RoleProfile).where(RoleProfile.id == profile_id))


def test_runtime_microsecond_clipping_and_report_queries_share_one_snapshot(accounts):
    from sqlalchemy import event

    from app.db import engine

    names, password, sids = accounts
    sid = sids[0]
    ingest_one(
        sid,
        [
            sample(external_id="micro-1", ended_at=STAMP + timedelta(microseconds=100000)),
            sample(
                external_id="micro-2",
                state="offline",
                started_at=STAMP + timedelta(microseconds=100000),
                ended_at=STAMP + timedelta(microseconds=300000),
            ),
        ],
    )
    inserted = False

    def after_group_query(connection, cursor, statement, parameters, context, executemany):
        nonlocal inserted
        if inserted or "GROUP BY" not in statement or "runtime_intervals" not in statement:
            return
        inserted = True
        # Another committed writer changes the data between grouped durations and list/count.
        ingest_one(
            sid,
            [
                sample(
                    external_id="micro-late",
                    started_at=STAMP + timedelta(microseconds=300000),
                    ended_at=STAMP + timedelta(microseconds=400000),
                )
            ],
        )

    event.listen(engine, "after_cursor_execute", after_group_query)
    try:
        with TestClient(app, client=("runtime-snapshot-" + secrets.token_hex(8), 1)) as client:
            assert (
                client.post(
                    "/api/v1/auth/login", json={"username": names[0], "password": password}
                ).status_code
                == 200
            )
            query = window(sid, end_at=(STAMP + timedelta(seconds=1)).isoformat())
            first = client.get(BASE, params=query).json()
            assert inserted and first["total"] == 2 and len(first["items"]) == 2
            axis = first["streams"][0]["axes"][0]
            assert Decimal(axis["reported_seconds"]) == Decimal("0.3")
            assert Decimal(axis["unreported_seconds"]) == Decimal("0.7")
            second = client.get(BASE, params=query).json()
            assert second["total"] == 3 and Decimal(
                second["streams"][0]["axes"][0]["reported_seconds"]
            ) == Decimal("0.4")
            clipped = client.get(
                BASE,
                params=window(
                    sid,
                    start_at=(STAMP + timedelta(microseconds=50000)).isoformat(),
                    end_at=(STAMP + timedelta(microseconds=250000)).isoformat(),
                ),
            ).json()
            assert [Decimal(item["observed_seconds"]) for item in clipped["items"]] == [
                Decimal("0.05"),
                Decimal("0.15"),
            ]
            assert Decimal(clipped["streams"][0]["axes"][0]["reported_seconds"]) == Decimal("0.2")
    finally:
        event.remove(engine, "after_cursor_execute", after_group_query)
