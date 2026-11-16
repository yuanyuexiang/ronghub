import secrets
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.main import app
from app.models import Audit, EnergyRecord, Station, User


def test_observed_energy_scope_missing_days_and_csv(accounts, staff):
    names, password, stations = accounts
    clients = [TestClient(app, client=("test-" + secrets.token_hex(6), 1)) for _ in range(3)]
    uid = None
    try:
        with SessionLocal.begin() as db:
            uid = db.scalar(select(User.id).where(User.username == names[0]))
            db.get(Station, stations[0]).name = '=HYPERLINK("bad")'
            for i, sid in enumerate(stations):
                db.add(
                    EnergyRecord(
                        station_id=sid,
                        source="test-stat",
                        external_id=secrets.token_hex(8),
                        day=date(2026, 10, 1),
                        kwh=Decimal("10.123456") if i == 0 else Decimal(999),
                    )
                )
        for c, name in zip(clients, [names[0], names[1], staff[0][1]], strict=True):
            assert (
                c.post(
                    "/api/v1/auth/login", json={"username": name, "password": password}
                ).status_code
                == 200
            )
        op, other, assoc = clients
        url = "/api/v1/statistics?start_date=2026-10-01&end_date=2026-10-02"
        result = op.get(url)
        assert result.status_code == 200
        data = result.json()
        assert data["total_kwh"] == "10.123456"
        assert data["daily"][1]["kwh"] is None
        assert data["daily"][0]["stations"] == 1
        assert data["previous_period"]["kwh"] is None
        assert len(data["ranking"]) == 1
        assert other.get(url).json()["total_kwh"] == "999.000000"
        export = op.get("/api/v1/statistics/export?start_date=2026-10-01&end_date=2026-10-02")
        assert export.status_code == 200
        assert "'=HYPERLINK" in export.text and "999.000000" not in export.text
        analysis = op.get(
            "/api/v1/statistics/analysis/export?start_date=2026-10-01&end_date=2026-10-02&granularity=month"
        )
        assert analysis.status_code == 200
        assert "10.123456" in analysis.text and "999.000000" not in analysis.text
        assert "2026-10" in analysis.text
        assert (
            assoc.get(
                "/api/v1/statistics/analysis/export?start_date=2026-10-01&end_date=2026-10-02"
            ).status_code
            == 403
        )
        assert op.get(url + "&granularity=quarter").status_code == 422
        assert (
            assoc.get(
                "/api/v1/statistics/export?start_date=2026-10-01&end_date=2026-10-02"
            ).status_code
            == 403
        )
        assert (
            op.get("/api/v1/statistics?start_date=2020-01-01&end_date=2026-10-02").status_code
            == 422
        )
    finally:
        for c in clients:
            c.close()
        with SessionLocal.begin() as db:
            db.execute(delete(EnergyRecord).where(EnergyRecord.station_id.in_(stations)))
            db.execute(
                delete(Audit).where(Audit.action == "statistics.export", Audit.user_id == uid)
            )


def test_period_buckets_comparisons_and_data_quality(accounts):
    from app import statistics

    names, _, stations = accounts
    try:
        with SessionLocal.begin() as db:
            user = db.scalar(select(User).where(User.username == names[0]))
            for day, amount in [
                (date(2024, 11, 1), "10.000001"),
                (date(2024, 11, 2), "20"),
                (date(2024, 10, 1), "5"),
                (date(2024, 10, 2), "10"),
                (date(2023, 11, 1), "10"),
                (date(2023, 11, 2), "10"),
            ]:
                db.add(
                    EnergyRecord(
                        station_id=stations[0],
                        source="test-period",
                        external_id=secrets.token_hex(8),
                        day=day,
                        kwh=Decimal(amount),
                    )
                )
            db.add(
                EnergyRecord(
                    station_id=stations[1],
                    source="test-period",
                    external_id=secrets.token_hex(8),
                    day=date(2024, 11, 1),
                    kwh=999,
                )
            )
            db.flush()
            report = statistics.report(
                db,
                user,
                date(2024, 11, 1),
                date(2024, 11, 2),
                granularity="month",
                dimension="district",
            )
            assert report["coverage"]["expected_station_days"] == 2
            assert report["coverage"]["complete_daily_observations"] is True
            assert report["comparisons"]["year_over_year"]["observed_change_percent"] == "50.00"
            assert (
                report["comparisons"]["period_over_period"]["observed_change_percent"] == "100.00"
            )
            assert report["comparisons"]["year_over_year"]["source_completeness_verified"] is False
            assert report["timeline"][0]["period"] == "2024-11"
            assert report["dimension_ranking"][0]["kwh"] == "30.000001"
            assert report["dimension_ranking"][0]["station_count"] == 1
            wider = statistics.report(
                db,
                user,
                date(2024, 10, 1),
                date(2024, 12, 1),
                granularity="month",
                dimension="operator",
            )
            assert wider["timeline"][2]["kwh"] is None
            assert wider["timeline"][2]["records"] == 0
            assert (
                wider["timeline"][0]["comparisons"]["year_over_year"]["observed_change_percent"]
                is None
            )
            annual = statistics.report(
                db, user, date(2023, 11, 1), date(2024, 10, 31), granularity="year"
            )
            assert [b["period"] for b in annual["timeline"]] == ["2023", "2024"]
            duplicate = EnergyRecord(
                station_id=stations[0],
                source="test-period",
                external_id=secrets.token_hex(8),
                day=date(2024, 11, 1),
                kwh=1,
            )
            db.add(duplicate)
            db.flush()
            duplicated = statistics.report(
                db, user, date(2024, 11, 1), date(2024, 11, 2), granularity="month"
            )
            assert duplicated["coverage"]["duplicate_records"] == 1
            assert duplicated["comparisons"]["year_over_year"]["observed_change_percent"] is None
            assert "多条记录" in duplicated["comparisons"]["year_over_year"]["unavailable_reason"]
            db.delete(duplicate)
            old = db.scalar(
                select(EnergyRecord).where(
                    EnergyRecord.station_id == stations[0], EnergyRecord.day == date(2023, 11, 1)
                )
            )
            old.source = "test-different-source"
            db.flush()
            mixed = statistics.report(
                db, user, date(2024, 11, 1), date(2024, 11, 2), granularity="month"
            )
            assert "来源" in mixed["comparisons"]["year_over_year"]["unavailable_reason"]
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(EnergyRecord).where(EnergyRecord.station_id.in_(stations)))


def test_leap_clamping_zero_baseline_and_partial_period(accounts):
    from datetime import UTC, datetime
    from unittest.mock import patch

    from app import statistics

    names, _, stations = accounts
    try:
        with SessionLocal.begin() as db:
            user = db.scalar(select(User).where(User.username == names[0]))
            for day, amount in [
                (date(2024, 2, 29), 10),
                (date(2023, 2, 28), 0),
                (date(2024, 1, 29), 5),
            ]:
                db.add(
                    EnergyRecord(
                        station_id=stations[0],
                        source="test-leap",
                        external_id=secrets.token_hex(8),
                        day=day,
                        kwh=amount,
                    )
                )
            db.flush()
            result = statistics.report(
                db, user, date(2024, 2, 29), date(2024, 2, 29), granularity="month"
            )
            comparison = result["comparisons"]["year_over_year"]
            assert comparison["period"]["start_date"] == "2023-02-28"
            assert comparison["period"]["kwh"] == "0.000000"
            assert comparison["observed_change_percent"] is None
            assert "为零" in comparison["unavailable_reason"]
            assert (
                result["comparisons"]["period_over_period"]["observed_change_percent"] == "100.00"
            )
            with patch("app.statistics.now", return_value=datetime(2024, 2, 29, 3, tzinfo=UTC)):
                result = statistics.report(
                    db, user, date(2024, 2, 29), date(2024, 2, 29), granularity="month"
                )
                assert (
                    "尚未结束" in result["comparisons"]["period_over_period"]["unavailable_reason"]
                )
            assert statistics.shift_months(date(2024, 3, 31), -1) == date(2024, 2, 29)
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(EnergyRecord).where(EnergyRecord.station_id.in_(stations)))
