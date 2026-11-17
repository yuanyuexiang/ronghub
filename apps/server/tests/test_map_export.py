import csv
import io
import secrets
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, insert, select
from test_geography import geo_fixture as geo_fixture  # noqa: PLC0414

from app.db import SessionLocal
from app.main import app
from app.models import Audit, RoleProfile, Station, User
from app.routers import geography as routes


def login(name, password):
    client = TestClient(
        app, client=("map-export-" + secrets.token_hex(8), 1), raise_server_exceptions=False
    )
    assert (
        client.post("/api/v1/auth/login", json={"username": name, "password": password}).status_code
        == 200
    )
    return client


def parsed(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["cache-control"] == "no-store"
    assert response.content.startswith(b"\xef\xbb\xbf")
    return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))


def test_export_matches_scoped_list_location_filters_unknowns_and_formula_protection(
    accounts, staff, geo_fixture
):
    prefix, extra, _ = geo_fixture
    clients = [login(name, accounts[1]) for name in [accounts[0][0], accounts[0][1], staff[0][1]]]
    try:
        owner, other, association = clients
        with SessionLocal.begin() as db:
            station = db.get(Station, accounts[2][0])
            station.name = "=SUM(1,2)-" + prefix
            station.address = "+unsafe"
            station.source_first_connected_at = datetime(2025, 1, 1, tzinfo=UTC)
        for location in ["all", "view", "mapped", "unlocated"]:
            query = {
                "q": prefix,
                "location": location,
                "west": 119.2,
                "east": 119.4,
                "south": 26,
                "north": 26.2,
            }
            rows = parsed(owner.get("/api/v1/map/export", params=query))
            listed = owner.get("/api/v1/map/stations", params={**query, "page_size": 100}).json()
            assert [int(row["场站编号"]) for row in rows] == [row["id"] for row in listed["items"]]
        rows = parsed(owner.get("/api/v1/map/export", params={"q": prefix, "location": "all"}))
        assert len(rows) == 4
        first = next(row for row in rows if int(row["场站编号"]) == accounts[2][0])
        assert first["场站名称"].startswith("'=SUM") and first["详细地址"] == "'+unsafe"
        assert first["来源首次接入时间（北京时间）"] == "2025-01-01 08:00:00+0800"
        assert first["当前有效星级"] == "3" and first["数据环境"] == "演示环境"
        unknown = next(row for row in rows if int(row["场站编号"]) == extra[2])
        assert unknown["经度（来源原值）"] == unknown["纬度（来源原值）"] == "暂无数据"
        assert unknown["来源首次接入时间（北京时间）"] == "暂无数据"
        assert unknown["未落点原因"] == "来源未提供坐标"
        assert (
            len(parsed(other.get("/api/v1/map/export", params={"q": prefix, "location": "all"})))
            == 1
        )
        assert (
            len(
                parsed(
                    association.get("/api/v1/map/export", params={"q": prefix, "location": "all"})
                )
            )
            == 5
        )
        assert (
            association.get(
                "/api/v1/statistics/export",
                params={"start_date": "2026-01-01", "end_date": "2026-01-02"},
            ).status_code
            == 403
        )
        assert (
            parsed(owner.get("/api/v1/map/export", params={"q": "does-not-exist-" + prefix})) == []
        )
        assert owner.get("/api/v1/map/export", params={"location": "invalid"}).status_code == 422
    finally:
        for client in clients:
            client.close()


def test_readonly_profile_does_not_gain_export_and_regional_scope_remains_effective(
    accounts, staff, geo_fixture
):
    prefix, _, profile_id = geo_fixture
    owner = login(accounts[0][0], accounts[1])
    reg = login(staff[0][0], accounts[1])
    try:
        with SessionLocal.begin() as db:
            user = db.scalar(select(User).where(User.username == accounts[0][0]))
            user.role_profile_id = profile_id
            uid = user.id
            rid = db.scalar(select(User.id).where(User.username == staff[0][0]))
            profile_before = list(db.get(RoleProfile, profile_id).permissions)
        assert owner.get("/api/v1/map/export").status_code == 403
        assert owner.get("/api/v1/map/stations").status_code == 200
        with SessionLocal.begin() as db:
            assert (
                db.get(RoleProfile, profile_id).permissions == profile_before == ["stations.read"]
            )
            db.get(RoleProfile, profile_id).permissions = ["stations.export"]
        assert (
            owner.get("/api/v1/map/export").status_code == 403
        )  # Read and export are both required.
        with SessionLocal.begin() as db:
            db.get(RoleProfile, profile_id).permissions = ["stations.read", "stations.export"]
        assert (
            len(parsed(owner.get("/api/v1/map/export", params={"q": prefix, "location": "all"})))
            == 4
        )
        for districts, expected in [(["鼓楼区"], 2), ([], 0)]:
            with SessionLocal.begin() as db:
                db.get(User, rid).allowed_districts = districts
            assert (
                len(parsed(reg.get("/api/v1/map/export", params={"q": prefix, "location": "all"})))
                == expected
            )
        with SessionLocal.begin() as db:
            db.get(User, uid).role_profile_id = None
    finally:
        owner.close()
        reg.close()


def test_limit_rejects_10001_without_partial_file_or_success_audit(accounts):
    prefix = "map-limit-" + secrets.token_hex(12)
    with SessionLocal() as db:
        oid = db.scalar(select(User.operator_id).where(User.username == accounts[0][0]))
        uid = db.scalar(select(User.id).where(User.username == accounts[0][0]))
    client = login(accounts[0][0], accounts[1])
    try:
        with SessionLocal.begin() as db:
            db.execute(
                insert(Station),
                [
                    {
                        "operator_id": oid,
                        "source": prefix,
                        "external_id": str(index),
                        "name": prefix,
                        "district": "测试",
                        "address": "Test",
                        "connectors": 0,
                        "power_kw": 0,
                    }
                    for index in range(10000)
                ],
            )
        rows = parsed(client.get("/api/v1/map/export", params={"q": prefix, "location": "all"}))
        assert len(rows) == 10000 and len({row["场站编号"] for row in rows}) == 10000
        with SessionLocal.begin() as db:
            db.add(
                Station(
                    operator_id=oid,
                    source=prefix,
                    external_id="overflow",
                    name=prefix,
                    district="测试",
                    address="Test",
                    connectors=0,
                    power_kw=0,
                )
            )
        response = client.get("/api/v1/map/export", params={"q": prefix, "location": "all"})
        assert response.status_code == 422 and "10000" in response.json()["detail"]
        assert "content-disposition" not in response.headers
        with SessionLocal() as db:
            logs = list(
                db.scalars(
                    select(Audit).where(Audit.user_id == uid, Audit.action == "stations.export")
                )
            )
            assert len(logs) == 1 and logs[0].detail["rows"] == 10000
    finally:
        client.close()
        with SessionLocal.begin() as db:
            db.execute(delete(Station).where(Station.operator_id == oid, Station.source == prefix))


def test_failed_csv_projection_never_records_success(accounts, monkeypatch):
    client = login(accounts[0][0], accounts[1])
    try:
        with SessionLocal() as db:
            uid = db.scalar(select(User.id).where(User.username == accounts[0][0]))
            before = db.scalar(
                select(func.count())
                .select_from(Audit)
                .where(Audit.user_id == uid, Audit.action == "stations.export")
            )

        def fail(values):
            raise RuntimeError("CSV response rejected")

        monkeypatch.setattr(routes, "csv_response", fail)
        assert client.get("/api/v1/map/export", params={"location": "all"}).status_code == 500
        with SessionLocal() as db:
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(Audit)
                    .where(Audit.user_id == uid, Audit.action == "stations.export")
                )
                == before
            )
    finally:
        client.close()
