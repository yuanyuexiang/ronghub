from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import delete
from test_business_exports import login

from app.db import SessionLocal
from app.models import Connector, EnergyRecord, Equipment, Station


def test_monitoring_and_daily_ranking_remain_scoped_and_preserve_unknowns(accounts):
    names, password, sids = accounts
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    eids = []
    with SessionLocal.begin() as db:
        for index, sid in enumerate(sids):
            db.get(Station, sid).station_type = "charging"
            db.get(Station, sid).status = "operating"
            equipment = Equipment(
                station_id=sid,
                external_id="view-device",
                name="=monitor-device",
                power_kw=Decimal(30),
            )
            db.add(equipment)
            db.flush()
            eids.append(equipment.id)
            db.add(Connector(equipment_id=equipment.id, external_id="c1", status="charging"))
            db.add(
                EnergyRecord(
                    station_id=sid,
                    source="test-views",
                    external_id=f"view-{sid}-prior",
                    day=today - timedelta(days=1),
                    kwh=Decimal(10) + index,
                )
            )
            db.add(
                EnergyRecord(
                    station_id=sid,
                    source="second-source",
                    external_id=f"view-{sid}-second",
                    day=today - timedelta(days=1),
                    kwh=Decimal(0),
                )
            )
    try:
        client = login(names[0], password)
        view = client.get("/api/v1/page-views/monitoring")
        assert view.status_code == 200, view.text
        data = view.json()
        assert (
            data["total"] == 1 and data["station_count"] == 1 and data["states"] == {"charging": 1}
        )
        assert data["items"][0]["station_id"] == sids[0] and data["station_types"]["charging"] == 1
        company = client.get("/api/v1/companies").json()["items"]
        assert len(company) == 1
        assert company[0]["station_count"] == 1 and company[0]["equipment_count"] == 1
        absent = client.get("/api/v1/page-views/monitoring?element=electrical").json()
        assert absent["items"] == [] and absent["electrical_count"] is None
        csv = client.get("/api/v1/page-views/monitoring/export")
        assert csv.status_code == 200 and "'=monitor-device" in csv.content.decode("utf-8-sig")
        rank = client.get("/api/v1/page-views/station-ranking").json()
        assert rank["total"] == 2 and rank["items"][0]["yesterday_kwh"] == "10.000000"
        assert rank["items"][0]["today_kwh"] is None
        assert rank["items"][1]["yesterday_kwh"] == "0.000000"
        daily = client.get(
            "/api/v1/page-views/daily-energy",
            params={"start_date": str(today - timedelta(days=1)), "end_date": str(today)},
        )
        assert daily.status_code == 200, daily.text
        rows = daily.json()["items"]
        assert len(rows) == 2 and all(r["subsidy_amount"] is None for r in rows)
        assert {r["source"] for r in rows} == {"test-views", "second-source"}
        assert (
            client.get(
                "/api/v1/page-views/daily-energy?start_date=2026-11-21&end_date=2026-11-20"
            ).status_code
            == 422
        )
        assert client.get("/api/v1/page-views/monitoring?page_size=101").status_code == 422
        assert client.get("/api/v1/page-views/unknown").status_code in (403, 404)
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(EnergyRecord).where(EnergyRecord.station_id.in_(sids)))
            db.execute(delete(Connector).where(Connector.equipment_id.in_(eids)))
            db.execute(delete(Equipment).where(Equipment.id.in_(eids)))


def test_station_application_exact_filter_is_intersected_with_operator_scope(accounts):
    names, password, sids = accounts
    client = login(names[0], password)
    # Operating state is not needed for access drafts.
    csrf = client.get("/api/v1/auth/me").json()["csrf_token"]
    created = client.post(
        "/api/v1/applications",
        headers={"x-csrf-token": csrf},
        json={"station_id": sids[0], "kind": "access", "payload": {}},
    )
    assert created.status_code == 201, created.text
    own = client.get("/api/v1/applications", params={"station_id": sids[0]}).json()
    assert [row["id"] for row in own["items"]] == [created.json()["id"]]
    assert client.get("/api/v1/applications", params={"station_id": sids[1]}).json()["items"] == []
    assert client.get("/api/v1/applications?station_id=0").status_code == 422
