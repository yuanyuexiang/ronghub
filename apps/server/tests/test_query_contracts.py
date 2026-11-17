import secrets
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, func, select
from test_rating import published_rating
from test_rating import rating_fixture as rating_fixture  # noqa: PLC0414

from app import rating_results
from app.db import SessionLocal, engine
from app.main import app
from app.models import Audit, EnergyRecord, RatingApplication, Station
from app.query_contracts import EnergyStatistics
from app.routers import statistics as routes


@pytest.fixture()
def case(accounts, staff):
    names, password, stations = accounts
    clients = [
        TestClient(app, client=("query-" + secrets.token_hex(8), 1), raise_server_exceptions=False)
        for _ in range(3)
    ]
    try:
        for client, name in zip(clients, [names[0], names[1], staff[0][0]], strict=True):
            assert (
                client.post(
                    "/api/v1/auth/login", json={"username": name, "password": password}
                ).status_code
                == 200
            )
        with SessionLocal.begin() as db:
            for index, sid in enumerate(stations):
                station = db.get(Station, sid)
                station.name = "query-old" if index == 0 else "query-other"
                station.longitude, station.latitude = "119.300000", "26.080000"
                station.coordinate_system = "wgs84"
                db.add(
                    EnergyRecord(
                        station_id=sid,
                        source="query-fixture",
                        external_id=secrets.token_hex(12),
                        day=date(2026, 10, 1),
                        kwh=Decimal("5.123456") if index == 0 else Decimal(999),
                    )
                )
        yield {"clients": clients, "stations": stations}
    finally:
        for client in clients:
            client.close()
        with SessionLocal.begin() as db:
            db.execute(delete(EnergyRecord).where(EnergyRecord.station_id.in_(stations)))


@pytest.mark.parametrize("path", ["/api/v1/map", "/api/v1/map/stations"])
def test_map_counts_cells_and_items_share_snapshot_under_actual_concurrent_update(case, path):
    sid = case["stations"][0]
    changed = False

    def change_after_count(connection, cursor, statement, parameters, context, executemany):
        nonlocal changed
        if not changed and "FROM stations" in statement and "count(" in statement:
            changed = True
            with SessionLocal.begin() as other:
                station = other.get(Station, sid)
                station.name = "query-new"
                station.longitude = station.latitude = None

    event.listen(engine, "after_cursor_execute", change_after_count)
    try:
        response = case["clients"][0].get(path, params={"q": "query-old", "zoom": 19})
    finally:
        event.remove(engine, "after_cursor_execute", change_after_count)
    assert changed and response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert len(data["items"]) == 1 and data["items"][0]["name"] == "query-old"
    assert data["items"][0]["longitude"] == "119.300000"
    if path.endswith("stations"):
        assert data["total"] == 1
    else:
        assert data["matching"] == data["mappable"] == data["in_view"] == 1
        assert data["unplottable"] == 0 and data["items"][0]["kind"] == "station"
    with SessionLocal() as db:
        assert db.get(Station, sid).name == "query-new" and db.get(Station, sid).longitude is None


@pytest.mark.parametrize(
    "path",
    ["/api/v1/statistics", "/api/v1/statistics/export", "/api/v1/statistics/analysis/export"],
)
def test_energy_cube_and_station_ranking_share_snapshot_under_actual_source_revision(
    case, path, monkeypatch
):
    changed = False
    projected = []
    original = EnergyStatistics.model_validate

    def capture(value):
        projected.append(value)
        return original(value)

    monkeypatch.setattr(EnergyStatistics, "model_validate", capture)

    def revise_after_daily_cube(connection, cursor, statement, parameters, context, executemany):
        nonlocal changed
        if not changed and "array_agg" in statement and "FROM energy_records" in statement:
            changed = True
            with SessionLocal.begin() as other:
                row = other.scalar(
                    select(EnergyRecord).where(EnergyRecord.station_id == case["stations"][0])
                )
                row.kwh = Decimal("99.000000")

    event.listen(engine, "after_cursor_execute", revise_after_daily_cube)
    try:
        response = case["clients"][0].get(
            path, params={"start_date": "2026-10-01", "end_date": "2026-10-02"}
        )
    finally:
        event.remove(engine, "after_cursor_execute", revise_after_daily_cube)
    assert changed and response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    if path == "/api/v1/statistics":
        data = response.json()
    else:
        assert response.headers["content-type"].startswith("text/csv")
        assert response.content.startswith(b"\xef\xbb\xbf")
        assert "5.123456" in response.text and "999.000000" not in response.text
        data = projected[0]
    assert data["total_kwh"] == data["ranking"][0]["kwh"] == "5.123456"
    assert data["daily"][1]["kwh"] is None and data["previous_period"]["kwh"] is None
    with SessionLocal() as db:
        assert db.scalar(
            select(EnergyRecord.kwh).where(EnergyRecord.station_id == case["stations"][0])
        ) == Decimal(99)


@pytest.mark.parametrize(
    "path", ["/api/v1/statistics/export", "/api/v1/statistics/analysis/export"]
)
@pytest.mark.parametrize("failure", ["projection", "response"])
def test_csv_failure_never_commits_success_audit(case, path, failure, monkeypatch):
    with SessionLocal() as db:
        before = db.scalar(
            select(func.count()).select_from(Audit).where(Audit.action == "statistics.export")
        )

    def fail(*args, **kwargs):
        raise RuntimeError("Response unavailable before commit")

    if failure == "projection":
        monkeypatch.setattr(EnergyStatistics, "model_validate", fail)
    else:
        monkeypatch.setattr(routes, "csv_response", fail)
    response = case["clients"][0].get(
        path, params={"start_date": "2026-10-01", "end_date": "2026-10-02"}
    )
    assert response.status_code == 500
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count()).select_from(Audit).where(Audit.action == "statistics.export")
            )
            == before
        )


def test_publication_page_keeps_same_snapshot_and_one_beijing_day(
    case, rating_fixture, monkeypatch
):
    uid, sid, standard = rating_fixture
    day = rating_results.local_day()
    with SessionLocal.begin() as db:
        publication = published_rating(db, uid, sid, standard)
        publication.valid_until = day
        aid = publication.id
    calls = 0

    def crossing_midnight():
        nonlocal calls
        calls += 1
        return day if calls == 1 else day + timedelta(days=1)

    monkeypatch.setattr(rating_results, "local_day", crossing_midnight)
    changed = False

    def expire_after_total(connection, cursor, statement, parameters, context, executemany):
        nonlocal changed
        if not changed and "count(" in statement and "FROM rating_applications" in statement:
            changed = True
            with SessionLocal.begin() as other:
                other.get(RatingApplication, aid).valid_until = day - timedelta(days=1)
                other.get(Station, sid).name = "query-new"

    event.listen(engine, "after_cursor_execute", expire_after_total)
    try:
        response = case["clients"][0].get(
            "/api/v1/ratings/publications",
            params={"q": "query-old", "result_state": "valid", "stars": 3},
        )
    finally:
        event.remove(engine, "after_cursor_execute", expire_after_total)
    assert response.status_code == 200 and changed and calls == 1
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["total"] == 1 and len(data["items"]) == 1
    assert data["items"][0]["station_name"] == "query-old"
    assert data["items"][0]["effective_stars"] == 3 and data["items"][0]["result_state"] == "valid"
    assert data["items"][0]["show_association_mark"]
    assert "非匿名" in data["scope"]


def test_query_unknowns_media_and_pg_integer_bounds_are_explicit(case):
    owner, other, _ = case["clients"]
    with SessionLocal.begin() as db:
        station = db.get(Station, case["stations"][0])
        station.longitude = station.latitude = None
    response = owner.get("/api/v1/map/stations", params={"location": "unlocated"})
    item = response.json()["items"][0]
    assert item["longitude"] is None and item["latitude"] is None
    assert item["effective_stars"] is None and item["source_first_connected_at"] is None
    assert item["unplottable_reason"] == "missing_coordinates"
    assert {"id", "name", "longitude", "latitude"}.issubset(item)
    assert (
        other.get("/api/v1/map/stations", params={"q": "query-old", "location": "all"}).json()[
            "total"
        ]
        == 0
    )
    assert owner.get("/api/v1/map/options").headers["cache-control"] == "no-store"
    for path, params in [
        ("/api/v1/map/stations", {"page": 2147483648}),
        ("/api/v1/map", {"operator_id": 2147483648}),
        ("/api/v1/ratings/publications", {"page": 2147483648}),
        (
            "/api/v1/statistics",
            {"start_date": "2026-10-01", "end_date": "2026-10-02", "operator_id": 2147483648},
        ),
    ]:
        assert owner.get(path, params=params).status_code == 422
    for path in ["/api/v1/statistics/export", "/api/v1/statistics/analysis/export"]:
        media = app.openapi()["paths"][path]["get"]["responses"]["200"]["content"]
        assert set(media) == {"text/csv"}


def test_map_and_publication_search_treat_percent_underscore_backslash_literally(
    case, rating_fixture
):
    uid, sid, standard = rating_fixture
    with SessionLocal.begin() as db:
        published_rating(db, uid, sid, standard)
    owner = case["clients"][0]
    for path in ["/api/v1/map/stations", "/api/v1/ratings/publications"]:
        assert owner.get(path, params={"q": "%"}).json()["total"] == 0
    with SessionLocal.begin() as db:
        db.get(Station, sid).name = "literal%_\\owned"
    for path in ["/api/v1/map/stations", "/api/v1/ratings/publications"]:
        data = owner.get(path, params={"q": "%_\\"}).json()
        assert data["total"] == 1
        assert data["items"][0].get("station_id", data["items"][0]["id"]) == sid
