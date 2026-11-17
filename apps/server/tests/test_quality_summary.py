import csv
import io
import secrets
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select
from test_quality import BASE, STAMP, add_station, clean, client, fault, interval, stream, window

from app import faults, runtime
from app.db import SessionLocal
from app.models import Audit, Equipment, Station, User
from app.routers import quality as router


def test_summary_is_all_pages_weighted_and_separates_current_inventory_from_observations(
    accounts, staff
):
    names, password, sids = accounts
    prefix = "quality-summary-" + secrets.token_hex(6)
    extra, equipment_ids = [], []
    try:
        with SessionLocal.begin() as db:
            own, foreign = [db.get(Station, sid) for sid in sids]
            own.name, own.district = prefix + "one", "A"
            foreign.name, foreign.district = prefix + "foreign", "C"
            second = add_station(db, own.operator_id, "test", prefix + "two", "B")
            empty = add_station(db, own.operator_id, "test", prefix + "empty", "B")
            extra = [second.id, empty.id]
            for sid, count in [(own.id, 2), (second.id, 1), (foreign.id, 4)]:
                for index in range(count):
                    row = Equipment(
                        station_id=sid, external_id=str(index), name="Current record", power_kw=1
                    )
                    db.add(row)
                    db.flush()
                    equipment_ids.append(row.id)
            for sid in [own.id, second.id]:
                runtime.ingest(
                    db,
                    "test",
                    own.operator_id,
                    db.get(Station, sid),
                    [interval("same-device-" + str(sid))],
                    is_demo=True,
                )
            faults.ingest(
                db,
                "test",
                own.operator_id,
                own,
                [fault("one", status="recovered", recovered_at=STAMP + timedelta(seconds=10))],
                is_demo=True,
            )
            faults.ingest(
                db,
                "test",
                own.operator_id,
                second,
                [
                    fault(
                        "three-" + str(i),
                        status="recovered",
                        recovered_at=STAMP + timedelta(seconds=30),
                    )
                    for i in range(3)
                ]
                + [fault("open"), fault("unknown", status="unknown")],
                is_demo=True,
            )
            db.commit()
        with (
            client(names[0], password) as op,
            client(names[1], password) as other,
            client(staff[0][0], password) as reg,
        ):
            query = window(q=prefix, group_by="station", page_size=1)
            data = op.get(BASE, params=query).json()
            assert len(data["items"]) == 1 and data["total"] == 3
            summary = data["summary"]
            assert summary["station_count"] == 3 and summary["inventory_equipment_count"] == 3
            observed = stream(summary)
            assert observed["observed_object_count"] == 2 and observed["stations_with_runtime"] == 2
            assert (
                observed["faults"]["recovered_events"] == 4
                and observed["faults"]["observed_events"] == 6
            )
            assert Decimal(observed["faults"]["average_recovery_elapsed_seconds"]) == 25
            assert data["source_completeness_verified"] is False
            for page in [2, 3, 999]:
                assert op.get(BASE, params={**query, "page": page}).json()["summary"] == summary
            for group_by in ["district", "operator"]:
                assert (
                    op.get(BASE, params={**query, "group_by": group_by}).json()["summary"]
                    == summary
                )
            assert reg.get(BASE, params=query).json()["summary"]["inventory_equipment_count"] == 7
            assert other.get(BASE, params=query).json()["summary"]["station_count"] == 1
            assert other.get(BASE, params=query).json()["summary"]["streams"] == []
            exported = op.get(BASE + "/export", params=window(q=prefix, group_by="station"))
            assert exported.status_code == 200 and exported.headers["cache-control"] == "no-store"
            rows = list(csv.DictReader(io.StringIO(exported.text.lstrip("\ufeff"))))
            total = [row for row in rows if row["记录类型"] == "查询总计"]
            assert len(total) == 1 and total[0]["当前台账设备数(非历史设备规模)"] == "3"
            assert Decimal(total[0]["已恢复样本平均经过秒数(非维修工时)"]) == 25
            assert len([row for row in rows if row["记录类型"] == "分组明细"]) == 3
            empty_result = op.get(BASE, params=window(q=prefix + "none")).json()
            assert empty_result["summary"]["station_count"] == 0
            assert (
                empty_result["summary"]["inventory_equipment_count"] == 0
                and empty_result["summary"]["streams"] == []
            )
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(Equipment).where(Equipment.id.in_(equipment_ids)))
        clean(extra)


@pytest.mark.parametrize("failure", ["projection", "response"])
def test_export_failure_cannot_leave_success_audit_and_retry_keeps_totals(
    accounts, monkeypatch, failure
):
    names, password, sids = accounts
    with SessionLocal() as db:
        uid = db.scalar(select(User.id).where(User.username == names[0]))
        sid = db.get(Station, sids[0]).id
    query = window(group_by="station", q="")
    with client(names[0], password) as op:
        with monkeypatch.context() as patch:
            if failure == "projection":
                original = router.quality.report

                def broken(*args, **kwargs):
                    result = original(*args, **kwargs)
                    result["summary"]["inventory_equipment_count"] = "not-an-integer"
                    return result

                patch.setattr(router.quality, "report", broken)
            else:

                def broken(*args, **kwargs):
                    raise ValueError("broken CSV response")

                patch.setattr(router, "Response", broken)
            with pytest.raises(ValueError):
                op.get(BASE + "/export", params=query)
        with SessionLocal() as db:
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(Audit)
                    .where(Audit.user_id == uid, Audit.action == "statistics.quality.export")
                )
                == 0
            )
        data = op.get(BASE + "/export", params=query)
        assert data.status_code == 200
        rows = list(csv.DictReader(io.StringIO(data.text.lstrip("\ufeff"))))
        assert any(row["分组编号"] == str(sid) for row in rows)
        assert sum(row["记录类型"] == "查询总计" for row in rows) == 1
