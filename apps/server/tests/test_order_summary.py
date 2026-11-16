import csv
import io
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import orders
from app.db import SessionLocal
from app.main import app
from app.models import ChargingOrder, Station, User

STAMP = datetime(2026, 11, 1, tzinfo=UTC)
WINDOW = {"start_at": STAMP.isoformat(), "end_at": (STAMP + timedelta(days=2)).isoformat()}


def record(key, **patch):
    return {
        "external_id": key,
        "equipment_external_id": "A",
        "status": "completed",
        "started_at": STAMP,
        "ended_at": STAMP + timedelta(hours=1),
        "source_updated_at": STAMP + timedelta(hours=2),
        "duration_seconds": 100,
        "kwh": "0.100000",
        "amount": "0.10",
        "currency": "CNY",
        **patch,
    }


def login(name, password):
    client = TestClient(app, client=("order-summary-" + secrets.token_hex(8), 1))
    assert (
        client.post("/api/v1/auth/login", json={"username": name, "password": password}).status_code
        == 200
    )
    return client


def test_order_rollup_coverage_timezone_source_separation_and_scope(accounts, staff):
    names, password, sids = accounts
    with SessionLocal.begin() as db:
        station = db.get(Station, sids[0])
        data = [
            record("known"),
            record("missing", kwh=None, amount=None, duration_seconds=None, currency=None),
            record("zero", equipment_external_id="B", kwh="0", amount="0", duration_seconds=0),
            record(
                "unmapped",
                equipment_external_id=None,
                kwh=None,
                amount=None,
                duration_seconds=None,
                currency=None,
            ),
            record("charging", status="charging", ended_at=None, kwh="999", amount="999"),
            record("interrupted", status="interrupted"),
            record("cancelled", status="cancelled"),
            record("unknown", status="unknown"),
            record(
                "tomorrow",
                started_at=STAMP + timedelta(hours=16),
                ended_at=STAMP + timedelta(hours=17),
                source_updated_at=STAMP + timedelta(hours=18),
                kwh="0.2",
                amount="0.20",
            ),
        ]
        orders.ingest(db, "test", station.operator_id, station, data, is_demo=True)
        # Report grouping must remain safe if independent adapters map multiple sources
        # or demo flags to a station; no source-completeness assumption is introduced.
        for source, demo, key, amount in [
            ("alternate", True, "second-source", "5.00"),
            ("test", False, "separate-demo-flag", "7.00"),
        ]:
            db.add(
                ChargingOrder(
                    station_id=station.id,
                    operator_id=station.operator_id,
                    source=source,
                    is_demo=demo,
                    **record(key, amount=amount, kwh=amount),
                )
            )
        other = db.get(Station, sids[1])
        orders.ingest(
            db, "test", other.operator_id, other, [record("foreign", amount="999")], is_demo=True
        )
        uid = db.scalar(select(User.id).where(User.username == names[0]))
    clients = [login(name, password) for name in [names[0], staff[0][0], staff[0][1]]]
    try:
        op, reg, association = clients
        assert association.get("/api/v1/orders/summary", params=WINDOW).status_code == 403
        assert association.get("/api/v1/orders/summary/export", params=WINDOW).status_code == 403
        response = op.get("/api/v1/orders/summary", params={**WINDOW, "dimension": "equipment"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 6 and body["source_completeness_verified"] is False
        assert body["timezone"] == "Asia/Shanghai"
        items = body["items"]
        a = next(
            row
            for row in items
            if row["source"] == "test"
            and row["is_demo"]
            and row["equipment_external_id"] == "A"
            and row["period_start"] == "2026-11-01"
        )
        assert a["observed_orders"] == 6 and a["completed_orders"] == 2
        assert a["kwh"] == {"value": "0.100000", "known_orders": 1, "missing_orders": 1}
        assert a["duration_seconds"] == {"value": "100", "known_orders": 1, "missing_orders": 1}
        assert a["amount"] == {"value": "0.10", "known_orders": 1, "missing_orders": 1}
        assert all(
            a[status + "_orders"] == 1
            for status in ["charging", "interrupted", "cancelled", "unknown"]
        )
        zero = next(row for row in items if row["equipment_external_id"] == "B")
        assert Decimal(zero["kwh"]["value"]) == 0 and zero["kwh"]["known_orders"] == 1
        missing = next(row for row in items if row["equipment_external_id"] is None)
        assert missing["kwh"] == {"value": None, "known_orders": 0, "missing_orders": 1}
        tomorrow = next(row for row in items if row["period_start"] == "2026-11-02")
        assert tomorrow["kwh"]["value"] == "0.200000"
        month = op.get("/api/v1/orders/summary", params={**WINDOW, "granularity": "month"}).json()
        assert month["total"] == 3
        total = next(row for row in month["items"] if row["source"] == "test" and row["is_demo"])
        assert total["observed_orders"] == 9 and total["completed_orders"] == 5
        assert total["kwh"] == {"value": "0.300000", "known_orders": 3, "missing_orders": 2}
        assert total["amount"] == {"value": "0.30", "known_orders": 3, "missing_orders": 2}
        assert (
            op.get("/api/v1/orders/summary", params={**WINDOW, "granularity": "year"}).json()[
                "items"
            ][0]["period_start"]
            == "2026-01-01"
        )
        active = op.get("/api/v1/orders/summary", params={**WINDOW, "status": "charging"}).json()[
            "items"
        ][0]
        assert active["completed_orders"] == 0 and active["kwh"] == {
            "value": None,
            "known_orders": 0,
            "missing_orders": 0,
        }
        assert (
            op.get("/api/v1/orders/summary", params={**WINDOW, "station_id": sids[1]}).json()[
                "total"
            ]
            == 0
        )
        assert (
            reg.get("/api/v1/orders/summary", params={**WINDOW, "station_id": sids[1]}).json()[
                "total"
            ]
            == 1
        )
        assert op.get("/api/v1/orders/summary", params={**WINDOW, "q": "%_"}).json()["total"] == 0
        assert (
            op.get("/api/v1/orders/summary", params={**WINDOW, "source": "missing"}).json()["total"]
            == 0
        )
        with SessionLocal.begin() as db:
            db.get(User, uid).allowed_districts = []
        assert op.get("/api/v1/orders/summary", params=WINDOW).json()["total"] == 0
        assert (
            len(
                list(
                    csv.reader(
                        io.StringIO(
                            op.get("/api/v1/orders/summary/export", params=WINDOW).content.decode(
                                "utf-8-sig"
                            )
                        )
                    )
                )
            )
            == 1
        )
    finally:
        for client in clients:
            client.close()


def test_rollup_pagination_csv_and_filter_bounds(accounts):
    names, password, sids = accounts
    with SessionLocal.begin() as db:
        station = db.get(Station, sids[0])
        rows = [record(f"history-{i}", equipment_external_id=f"pile-{i}") for i in range(122)]
        rows[0]["equipment_external_id"] = "=SUM(1,2)"
        orders.ingest(db, "test", station.operator_id, station, rows, is_demo=True)
    with login(names[0], password) as client:
        page = client.get(
            "/api/v1/orders/summary", params={**WINDOW, "dimension": "equipment", "page": 7}
        ).json()
        assert page["total"] == 122 and len(page["items"]) == 2
        exported = client.get(
            "/api/v1/orders/summary/export", params={**WINDOW, "dimension": "equipment"}
        )
        assert exported.status_code == 200
        csv_rows = list(csv.reader(io.StringIO(exported.content.decode("utf-8-sig"))))
        assert len(csv_rows) == 123 and any(
            row[csv_rows[0].index("源桩编码")] == "'=SUM(1,2)" for row in csv_rows[1:]
        )
        assert csv_rows[1][-1] == "False"
        for params in [
            {},
            {"start_at": WINDOW["start_at"]},
            {**WINDOW, "dimension": "invented"},
            {**WINDOW, "granularity": "hour"},
            {**WINDOW, "page_size": 101},
            {**WINDOW, "page": 0},
            {**WINDOW, "start_at": "2026-11-01T00:00:00"},
            {**WINDOW, "end_at": WINDOW["start_at"]},
        ]:
            assert client.get("/api/v1/orders/summary", params=params).status_code == 422
