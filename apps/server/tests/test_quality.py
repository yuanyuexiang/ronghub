import csv
import io
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import delete, event, insert, select

from app import faults, runtime
from app.db import SessionLocal, engine
from app.main import app
from app.models import Audit, FaultEvent, RoleProfile, RuntimeInterval, Station, User

STAMP = datetime(2026, 11, 1, tzinfo=UTC)
BASE = "/api/v1/statistics/quality"


def client(name, password):
    result = TestClient(app, client=("quality-" + secrets.token_hex(8), 1))
    assert (
        result.post("/api/v1/auth/login", json={"username": name, "password": password}).status_code
        == 200
    )
    return result


def window(**patch):
    return {
        "source": "test",
        "start_at": STAMP.isoformat(),
        "end_at": (STAMP + timedelta(hours=4)).isoformat(),
        **patch,
    }


def interval(key, **patch):
    return {
        "external_id": key,
        "equipment_external_id": "E",
        "connector_external_id": "C1",
        "dimension": "connectivity",
        "state": "online",
        "started_at": STAMP,
        "ended_at": STAMP + timedelta(hours=1),
        "source_updated_at": STAMP + timedelta(days=1),
        **patch,
    }


def fault(key, **patch):
    return {
        "external_id": key,
        "status": "active",
        "started_at": STAMP,
        "source_updated_at": STAMP + timedelta(days=1),
        **patch,
    }


def add_station(db, oid, source, name, district):
    row = Station(
        operator_id=oid,
        source=source,
        external_id="quality-" + secrets.token_hex(12),
        name=name,
        district=district,
        address="fixture",
        power_kw=1,
        connectors=1,
    )
    db.add(row)
    db.flush()
    return row


def clean(ids):
    with SessionLocal.begin() as db:
        db.execute(delete(RuntimeInterval).where(RuntimeInterval.station_id.in_(ids)))
        db.execute(delete(FaultEvent).where(FaultEvent.station_id.in_(ids)))
        db.execute(delete(Station).where(Station.id.in_(ids)))


def stream(group, demo=True):
    return next(value for value in group["streams"] if value["is_demo"] == demo)


def test_quality_cohort_axes_raw_fault_scope_unknown_and_identity(accounts, staff):
    names, password, sids = accounts
    prefix = "quality-" + secrets.token_hex(8)
    extras = []
    profile_id = None
    try:
        with SessionLocal.begin() as db:
            own, foreign = [db.get(Station, sid) for sid in sids]
            for station in [own, foreign]:
                station.name = prefix + str(station.id)
                station.district = "=SUM(1,2)"
            uid = db.scalar(select(User.id).where(User.username == names[0]))
            empty = add_station(db, own.operator_id, "test", prefix + "empty", "=SUM(1,2)")
            only_fault = add_station(db, own.operator_id, "test", prefix + "fault-only", "D2")
            unobserved = add_station(db, own.operator_id, "test", prefix + "unobserved", "D3")
            extras = [empty.id, only_fault.id, unobserved.id]
            runtime.ingest(
                db,
                "test",
                own.operator_id,
                own,
                [
                    interval("online"),
                    interval(
                        "unknown",
                        state="unknown",
                        started_at=STAMP + timedelta(hours=1),
                        ended_at=STAMP + timedelta(hours=2),
                    ),
                    interval("faulted", dimension="operation", state="faulted"),
                    interval(
                        "charging",
                        dimension="operation",
                        state="charging",
                        started_at=STAMP + timedelta(hours=1),
                        ended_at=STAMP + timedelta(hours=2),
                    ),
                    interval(
                        "other-connector",
                        connector_external_id="C2",
                        ended_at=STAMP + timedelta(hours=4),
                    ),
                    interval(
                        "equipment", connector_external_id="", ended_at=STAMP + timedelta(hours=4)
                    ),
                ],
                is_demo=True,
            )
            runtime.ingest(
                db,
                "test",
                own.operator_id,
                own,
                [interval("non-demo", ended_at=STAMP + timedelta(hours=4))],
                is_demo=False,
            )
            runtime.ingest(
                db,
                "test",
                foreign.operator_id,
                foreign,
                [interval("foreign", ended_at=STAMP + timedelta(hours=4))],
                is_demo=True,
            )
            faults.ingest(
                db,
                "test",
                own.operator_id,
                own,
                [
                    fault("zero", status="recovered", recovered_at=STAMP),
                    fault(
                        "micro", status="recovered", recovered_at=STAMP + timedelta(microseconds=2)
                    ),
                    fault("active"),
                    fault("unknown", status="unknown"),
                    fault(
                        "outside-window",
                        status="recovered",
                        started_at=STAMP - timedelta(hours=1),
                        recovered_at=STAMP + timedelta(seconds=30),
                    ),
                ],
                is_demo=True,
            )
            faults.ingest(
                db, "test", own.operator_id, only_fault, [fault("only-fault")], is_demo=True
            )
        with client(names[0], password) as op:
            response = op.get(BASE, params=window(q=prefix))
            assert response.status_code == 200, response.text
            data = response.json()
            assert (
                data["selected_station_count"] == 4
                and data["total"] == 3
                and data["faults_scope"] == "all_station_events_started_in_window"
            )
            groups = {group["key"]: group for group in data["items"]}
            first = groups["=SUM(1,2)"]
            assert first["station_count"] == 2
            demo = stream(first)
            assert (
                demo["observed_object_count"] == 2
                and demo["stations_with_runtime"] == 1
                and Decimal(demo["observed_object_window_seconds"]) == 28800
            )
            conn, operation = demo["axes"]
            assert tuple(
                Decimal(conn[key])
                for key in [
                    "reported_seconds",
                    "known_seconds",
                    "unknown_seconds",
                    "unreported_seconds",
                ]
            ) == (21600, 18000, 3600, 7200)
            assert (
                Decimal(operation["reported_seconds"]) == 7200
                and Decimal(operation["unreported_seconds"]) == 21600
            )
            assert (
                next(value for value in conn["states"] if value["state"] == "online")[
                    "share_of_reported_pct"
                ]
                == "83.33"
            )
            assert (
                demo["faults"]["observed_events"] == 4
                and demo["faults"]["recovered_events"] == 2
                and demo["faults"]["active_events"] == 1
                and demo["faults"]["unknown_events"] == 1
            )
            assert Decimal(demo["faults"]["average_recovery_elapsed_seconds"]) == Decimal(
                "0.000001"
            )
            assert Decimal(stream(first, False)["axes"][0]["reported_seconds"]) == 14400
            only = stream(groups["D2"])
            assert only["observed_object_count"] == 0 and only["faults"]["observed_events"] == 1
            assert all(
                state["share_of_reported_pct"] is None for state in only["axes"][0]["states"]
            )
            assert groups["D3"]["streams"] == []
            equipment = op.get(BASE, params=window(q=prefix, asset_scope="equipment")).json()
            assert (
                stream(next(group for group in equipment["items"] if group["key"] == "=SUM(1,2)"))[
                    "observed_object_count"
                ]
                == 1
            )
            assert (
                Decimal(
                    stream(
                        next(group for group in equipment["items"] if group["key"] == "=SUM(1,2)")
                    )["axes"][0]["reported_seconds"]
                )
                == 14400
            )
            assert (
                stream(next(group for group in equipment["items"] if group["key"] == "=SUM(1,2)"))[
                    "faults"
                ]
                == demo["faults"]
            )  # Faults are independently scoped station events, not a denominator.
            source = op.get(BASE + "/sources", params={"q": "test"}).json()
            assert source["items"] == [{"source": "test", "station_count": 4}]
            exported = op.get(BASE + "/export", params=window(q=prefix))
            rows = list(csv.reader(io.StringIO(exported.text.lstrip("\ufeff"))))
            assert exported.status_code == 200 and len(rows) == 5
            label_index = rows[0].index("当前分组名称")
            assert any(row[label_index] == "'=SUM(1,2)" for row in rows[1:])
            demo_index = rows[0].index("演示标识")
            objects_index = rows[0].index("本组观测源对象数(非设备总数)")
            empty_row = next(row for row in rows[1:] if row[label_index] == "D3")
            assert empty_row[demo_index] == "暂无数据" and empty_row[objects_index] == "暂无数据"
            assert (
                op.get(BASE, params=window(q=prefix, operator_id=foreign.operator_id)).json()[
                    "selected_station_count"
                ]
                == 0
            )
            assert (
                op.get(BASE, params=window(q=prefix, district="D2")).json()[
                    "selected_station_count"
                ]
                == 1
            )
            for patch in [
                {"source": " "},
                {"start_at": "2026-11-01T00:00:00"},
                {"end_at": STAMP.isoformat()},
                {"end_at": (STAMP + timedelta(days=367)).isoformat()},
                {"page_size": 101},
                {"asset_scope": "station"},
                {"group_by": "invalid"},
            ]:
                assert op.get(BASE, params=window(q=prefix, **patch)).status_code == 422
            with SessionLocal.begin() as db:
                profile = RoleProfile(
                    name="quality-read-" + secrets.token_hex(8),
                    base_role="operator",
                    permissions=["statistics.read"],
                )
                db.add(profile)
                db.flush()
                profile_id = profile.id
                db.get(User, uid).role_profile_id = profile_id
            assert op.get(BASE, params=window(q=prefix)).status_code == 200
            assert op.get(BASE + "/sources").status_code == 200
            assert op.get(BASE + "/export", params=window(q=prefix)).status_code == 403
            with SessionLocal.begin() as db:
                db.get(User, uid).allowed_districts = ["D2"]
            assert op.get(BASE, params=window(q=prefix)).json()["selected_station_count"] == 1
            with SessionLocal.begin() as db:
                db.get(User, uid).allowed_districts = []
            assert op.get(BASE, params=window(q=prefix)).json()["items"] == []
            assert op.get(BASE + "/sources").json()["items"] == []
        with client(staff[0][0], password) as reg:
            data = reg.get(BASE, params=window(q=prefix)).json()
            assert (
                data["selected_station_count"] == 5
                and stream(next(group for group in data["items"] if group["key"] == "=SUM(1,2)"))[
                    "observed_object_count"
                ]
                == 3
            )
        with client(staff[0][1], password) as association:
            assert association.get(BASE, params=window(q=prefix)).status_code == 403
            assert association.get(BASE + "/sources").status_code == 403
    finally:
        with SessionLocal.begin() as db:
            user = db.scalar(select(User).where(User.username == names[0]))
            user.role_profile_id = None
            user.allowed_districts = None
            db.flush()
            if profile_id:
                db.execute(delete(RoleProfile).where(RoleProfile.id == profile_id))
        clean(extras)


def test_quality_group_and_source_paging_literal_search_and_current_labels(accounts):
    names, password, sids = accounts
    prefix = "q-page-" + secrets.token_hex(6)
    extras = []
    try:
        with SessionLocal.begin() as db:
            own = db.get(Station, sids[0])
            own.source = prefix
            own.name = prefix + "%_"
            own.district = "D000"
            for index in range(1, 123):
                extras.append(
                    add_station(db, own.operator_id, prefix, prefix + str(index), f"D{index:03}").id
                )
            for index in range(1, 123):
                extras.append(
                    add_station(
                        db,
                        own.operator_id,
                        prefix + str(index),
                        prefix + "source" + str(index),
                        "other",
                    ).id
                )
        with client(names[0], password) as op:
            result = op.get(BASE, params=window(source=prefix, page=7)).json()
            assert (
                result["total"] == 123
                and len(result["items"]) == 3
                and result["items"][0]["key"] == "D120"
            )
            assert (
                op.get(BASE, params=window(source=prefix, q="%_")).json()["selected_station_count"]
                == 1
            )
            station = op.get(BASE, params=window(source=prefix, group_by="station", q="%_")).json()[
                "items"
            ][0]
            assert station["key"] == str(sids[0]) and station["label"] == prefix + "%_"
            operator = op.get(BASE, params=window(source=prefix, group_by="operator")).json()[
                "items"
            ]
            assert len(operator) == 1 and operator[0]["station_count"] == 123
            assert op.get(BASE + "/sources", params={"q": prefix, "page": 7}).json()["total"] == 123
            assert (
                len(op.get(BASE + "/sources", params={"q": prefix, "page": 7}).json()["items"]) == 3
            )
            with SessionLocal.begin() as db:
                db.get(Station, sids[0]).name = prefix + "renamed"
            assert (
                op.get(BASE, params=window(source=prefix, group_by="station", q="renamed")).json()[
                    "items"
                ][0]["label"]
                == prefix + "renamed"
            )
    finally:
        clean(extras)


def test_quality_cohort_counts_and_durations_share_repeatable_snapshot(accounts):
    names, password, sids = accounts
    prefix = "q-snapshot-" + secrets.token_hex(6)
    extras = []
    fired = False
    try:
        with SessionLocal.begin() as db:
            own = db.get(Station, sids[0])
            own.source = prefix
            own.name = prefix
            second = add_station(db, own.operator_id, prefix, prefix + "second", "other")
            extras.append(second.id)
            for station, value in [(own, 100000), (second, 200000)]:
                runtime.ingest(
                    db,
                    prefix,
                    own.operator_id,
                    station,
                    [interval(str(station.id), ended_at=STAMP + timedelta(microseconds=value))],
                    is_demo=True,
                )

        def after_count(connection, cursor, statement, parameters, context, executemany):
            nonlocal fired
            if (
                fired
                or "SELECT count(*)" not in statement
                or "FROM (SELECT stations.id AS id" not in statement
            ):
                return
            fired = True
            with SessionLocal.begin() as other:
                other.get(Station, extras[0]).source = "changed-" + prefix

        event.listen(engine, "after_cursor_execute", after_count)
        try:
            with client(names[0], password) as op:
                query = window(
                    source=prefix,
                    group_by="operator",
                    end_at=(STAMP + timedelta(seconds=1)).isoformat(),
                )
                first = op.get(BASE, params=query).json()
                assert (
                    fired
                    and first["selected_station_count"] == 2
                    and first["items"][0]["station_count"] == 2
                )
                assert Decimal(stream(first["items"][0])["axes"][0]["reported_seconds"]) == Decimal(
                    "0.3"
                )
                second = op.get(BASE, params=query).json()
                assert second["selected_station_count"] == 1 and Decimal(
                    stream(second["items"][0])["axes"][0]["reported_seconds"]
                ) == Decimal("0.1")
        finally:
            event.remove(engine, "after_cursor_execute", after_count)
    finally:
        clean(extras)


def test_quality_export_rejects_10001_groups_without_success_audit(accounts):
    names, password, sids = accounts
    prefix = "q-limit-" + secrets.token_hex(6)
    extras = []
    try:
        with SessionLocal.begin() as db:
            own = db.get(Station, sids[0])
            uid = db.scalar(select(User.id).where(User.username == names[0]))
            extras = list(
                db.scalars(
                    insert(Station).returning(Station.id),
                    [
                        {
                            "operator_id": own.operator_id,
                            "source": prefix,
                            "external_id": prefix + str(index),
                            "name": prefix + ("dual-" if index < 5001 else "single-") + str(index),
                            "district": f"D{index:05}",
                            "address": "fixture",
                            "power_kw": 1,
                            "connectors": 1,
                        }
                        for index in range(10001)
                    ],
                )
            )
            dual_ids = list(
                db.scalars(
                    select(Station.id).where(
                        Station.source == prefix,
                        Station.name.startswith(prefix + "dual-", autoescape=True),
                    )
                )
            )
            db.execute(
                insert(RuntimeInterval),
                [
                    {
                        "station_id": sid,
                        "operator_id": own.operator_id,
                        "source": prefix,
                        "is_demo": demo,
                        **interval(f"dual-{sid}-{demo}"),
                    }
                    for sid in dual_ids
                    for demo in [False, True]
                ],
            )
        with client(names[0], password) as op:
            assert op.get(BASE + "/export", params=window(source=prefix)).status_code == 422
            with SessionLocal() as db:
                assert (
                    db.scalar(
                        select(Audit.id).where(
                            Audit.user_id == uid, Audit.action == "statistics.quality.export"
                        )
                    )
                    is None
                )
            # 5001 groups fit the group cap, but two independent flags produce 10002 output rows.
            assert (
                op.get(
                    BASE + "/export", params=window(source=prefix, q=prefix + "dual-")
                ).status_code
                == 422
            )
            with SessionLocal() as db:
                assert (
                    db.scalar(
                        select(Audit.id).where(
                            Audit.user_id == uid, Audit.action == "statistics.quality.export"
                        )
                    )
                    is None
                )
            result = op.get(BASE + "/export", params=window(source=prefix, district="D10000"))
            assert (
                result.status_code == 200
                and len(list(csv.reader(io.StringIO(result.text.lstrip("\ufeff"))))) == 2
            )
    finally:
        clean(extras)
