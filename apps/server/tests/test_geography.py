import secrets
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.main import app
from app.models import (
    Connector,
    Equipment,
    RatingApplication,
    RatingStandard,
    RoleProfile,
    Station,
    User,
)
from app.rating_results import local_day


@pytest.fixture()
def geo_fixture(accounts):
    names, _, stations = accounts
    prefix = "geo-" + secrets.token_hex(5)
    extra = []
    standard_id = profile_id = None
    with SessionLocal.begin() as db:
        owner = db.scalar(select(User).where(User.username == names[0]))
        for index, sid in enumerate(stations):
            s = db.get(Station, sid)
            s.name = prefix + f"-{index}"
            s.district = "鼓楼区"
            s.longitude, s.latitude = "119.300000", "26.080000"
            s.coordinate_system, s.location_is_demo, s.station_type = "wgs84", True, "charging"
            s.status = "operating"
            s.usage_type, s.construction_site, s.site_state, s.source_sync_state = (
                "public",
                "public_institution",
                "normal",
                "active",
            )
        for index, system in enumerate(["wgs84", "gcj02", "unknown"]):
            s = Station(
                operator_id=owner.operator_id,
                source="test-geo",
                external_id=secrets.token_hex(8),
                name=prefix + f"-extra-{index}",
                district="晋安区",
                address="test",
                power_kw=240 if index == 0 else 60,
                connectors=1,
                status="operating",
                station_type="swapping" if index == 0 else "unknown",
                longitude="119.300010" if system != "unknown" else None,
                latitude="26.080010" if system != "unknown" else None,
                coordinate_system=system,
                location_is_demo=True,
            )
            db.add(s)
            db.flush()
            extra.append(s.id)
        equipment = Equipment(station_id=extra[0], external_id="e", name="test", power_kw=240)
        db.add(equipment)
        db.flush()
        db.add(Connector(equipment_id=equipment.id, external_id="c", status="fault"))
        standard = RatingStandard(code=prefix, version=1, name="test", rules={})
        db.add(standard)
        db.flush()
        standard_id = standard.id
        db.add(
            RatingApplication(
                station_id=stations[0],
                operator_id=owner.operator_id,
                standard_id=standard.id,
                status="published",
                initial_scores={},
                current_scores={},
                rule_snapshot={},
                calculation={"stars": 3},
                valid_until=local_day() + timedelta(days=30),
            )
        )
        profile = RoleProfile(name=prefix, base_role="operator", permissions=["stations.read"])
        db.add(profile)
        db.flush()
        profile_id = profile.id
    yield prefix, extra, profile_id
    with SessionLocal.begin() as db:
        owner = db.scalar(select(User).where(User.username == names[0]))
        owner.role_profile_id = None
        db.flush()
        db.execute(delete(RoleProfile).where(RoleProfile.id == profile_id))
        db.execute(delete(RatingApplication).where(RatingApplication.standard_id == standard_id))
        db.execute(delete(RatingStandard).where(RatingStandard.id == standard_id))
        equipment = select(Equipment.id).where(Equipment.station_id.in_(extra + stations))
        db.execute(delete(Connector).where(Connector.equipment_id.in_(equipment)))
        db.execute(delete(Equipment).where(Equipment.station_id.in_(extra + stations)))
        db.execute(delete(Station).where(Station.id.in_(extra)))


def test_map_clustering_scope_location_gaps_and_combined_filters(accounts, staff, geo_fixture):
    names, password, stations = accounts
    prefix, extra, _ = geo_fixture
    clients = [TestClient(app, client=("geo-" + secrets.token_hex(5), 1)) for _ in range(3)]
    try:
        owner, other, regulator = clients
        for client, name in zip(clients, [names[0], names[1], staff[0][0]], strict=True):
            assert (
                client.post(
                    "/api/v1/auth/login", json={"username": name, "password": password}
                ).status_code
                == 200
            )
        params = {"q": prefix, "west": 119.2, "south": 26, "east": 119.4, "north": 26.2, "zoom": 8}
        data = owner.get("/api/v1/map", params=params)
        assert data.status_code == 200, data.text
        data = data.json()
        assert (data["matching"], data["mappable"], data["unplottable"], data["in_view"]) == (
            4,
            2,
            2,
            2,
        )
        assert len(data["items"]) == 1 and data["items"][0]["kind"] == "cluster"
        assert data["items"][0]["count"] == 2
        assert other.get("/api/v1/map", params=params).json()["in_view"] == 1
        assert regulator.get("/api/v1/map", params=params).json()["in_view"] == 3
        unknown = owner.get(
            "/api/v1/map/stations", params={"q": prefix, "location": "unlocated"}
        ).json()
        assert unknown["total"] == 2
        assert {s["unplottable_reason"] for s in unknown["items"]} == {
            "missing_coordinates",
            "unsupported_coordinate_system",
        }
        fault = owner.get(
            "/api/v1/map/stations",
            params={
                "q": prefix,
                "runtime_state": "fault",
                "station_type": "swapping",
                "min_power": "200",
                "location": "view",
            },
        ).json()
        assert fault["total"] == 1 and fault["items"][0]["id"] == extra[0]
        assert fault["items"][0]["observed_connectors"] == 1
        starred = owner.get(
            "/api/v1/map/stations", params={"q": prefix, "stars": 3, "location": "all"}
        ).json()
        assert starred["total"] == 1 and starred["items"][0]["id"] == stations[0]
        assert starred["items"][0]["runtime_state"] == "unknown"
        assert starred["items"][0]["missing_connector_snapshots"] == 2
        outside = owner.get("/api/v1/map", params={**params, "west": 118, "east": 118.1}).json()
        assert outside["in_view"] == 0 and outside["matching"] == 4
        assert owner.get("/api/v1/map?west=120&east=119").status_code == 422
        assert owner.get("/api/v1/map?min_power=200&max_power=100").status_code == 422
        assert owner.get("/api/v1/map?limit=501").status_code == 422
        classified = owner.get(
            "/api/v1/map/stations",
            params={
                "q": prefix,
                "location": "all",
                "usage_type": "public",
                "construction_site": "public_institution",
                "site_state": "normal",
                "source_sync_state": "active",
            },
        ).json()
        assert classified["total"] == 1 and classified["items"][0]["id"] == stations[0]
        assert owner.get("/api/v1/map?usage_type=guessed").status_code == 422
        assert (
            owner.get("/api/v1/map/options").json()["classifications"]["site_state"]["maintenance"]
            == "维护中"
        )
        with SessionLocal.begin() as db:
            moved = db.get(Station, extra[0])
            moved.longitude, moved.latitude = "119.350000", "26.150000"
        limited = owner.get("/api/v1/map", params={**params, "zoom": 19, "limit": 1}).json()
        assert limited["truncated"] is True and limited["cell_count"] == 2
        assert limited["in_view"] == 2 and len(limited["items"]) == 1
        first = owner.get(
            "/api/v1/map/stations", params={"q": prefix, "location": "mapped", "page_size": 1}
        ).json()
        second = owner.get(
            "/api/v1/map/stations",
            params={"q": prefix, "location": "mapped", "page_size": 1, "page": 2},
        ).json()
        assert first["total"] == second["total"] == 2
        assert first["items"][0]["id"] != second["items"][0]["id"]
        with SessionLocal.begin() as db:
            rating = db.scalar(
                select(RatingApplication).where(RatingApplication.station_id == stations[0])
            )
            rating.valid_until = local_day() - timedelta(days=1)
        assert (
            owner.get(
                "/api/v1/map/stations", params={"q": prefix, "stars": 0, "location": "all"}
            ).json()["items"][0]["id"]
            == stations[0]
        )
    finally:
        for client in clients:
            client.close()


def test_map_district_and_rating_capability_are_not_bypassed(accounts, geo_fixture):
    names, password, _ = accounts
    prefix, _, profile_id = geo_fixture
    with TestClient(app, client=("geo-scope-" + secrets.token_hex(5), 1)) as client:
        assert (
            client.post(
                "/api/v1/auth/login", json={"username": names[0], "password": password}
            ).status_code
            == 200
        )
        with SessionLocal.begin() as db:
            user = db.scalar(select(User).where(User.username == names[0]))
            user.allowed_districts = ["鼓楼区"]
            user.role_profile_id = profile_id
        response = client.get("/api/v1/map", params={"q": prefix, "zoom": 19})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["matching"] == 1 and data["in_view"] == 1
        assert data["items"][0]["effective_stars"] is None
        assert client.get("/api/v1/map", params={"q": prefix, "stars": 3}).status_code == 403
        assert (
            client.get(
                "/api/v1/map/stations",
                params={"q": prefix, "district": "晋安区", "location": "all"},
            ).json()["total"]
            == 0
        )


def test_partial_offline_snapshots_are_not_whole_station_offline(accounts, geo_fixture):
    from app import geography

    names, _, stations = accounts
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.username == names[0]))
        equipment = Equipment(
            station_id=stations[0], external_id="offline", name="partial", power_kw=60
        )
        db.add(equipment)
        db.flush()
        db.add(Connector(equipment_id=equipment.id, external_id="one", status="offline"))
        db.flush()
        result = geography.output(
            db.execute(geography.query(db, user, {}).where(Station.id == stations[0])).one()
        )
        assert result["runtime_state"] == "unknown" and result["missing_connector_snapshots"] == 1
        db.add(Connector(equipment_id=equipment.id, external_id="two", status="offline"))
        db.flush()
        result = geography.output(
            db.execute(geography.query(db, user, {}).where(Station.id == stations[0])).one()
        )
        assert result["runtime_state"] == "offline"


def test_source_classifications_are_validated_and_omitted_fields_preserved():
    from datetime import UTC, datetime

    from app.station_metadata import values

    assert values({}) == {}
    result = values(
        {
            "usage_type": "bus",
            "construction_site": "roadside",
            "site_state": "maintenance",
            "source_sync_state": "removed",
            "source_first_connected_at": "2026-11-01T08:00:00+08:00",
        }
    )
    assert result["site_state"] == "maintenance"
    assert result["source_first_connected_at"].astimezone(UTC) == datetime(2026, 11, 1, tzinfo=UTC)
    for bad in [
        {"site_state": "operating"},
        {"usage_type": "charging"},
        {"source_first_connected_at": "2026-11-01T08:00:00"},
    ]:
        with pytest.raises(ValueError):
            values(bad)
