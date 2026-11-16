import secrets

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db import SessionLocal
from app.main import app
from app.models import Station


def test_scoped_station_pagination_and_combined_filters(accounts):
    names, password, stations = accounts
    prefix = "paged-" + secrets.token_hex(6)
    ids = []
    try:
        with SessionLocal.begin() as db:
            operator_id = db.get(Station, stations[0]).operator_id
            foreign_operator = db.get(Station, stations[1]).operator_id
            for i in range(25):
                s = Station(
                    operator_id=operator_id,
                    source="test-page",
                    external_id=f"{prefix}-{i}",
                    name=f"{prefix}-{i:02}",
                    district="鼓楼区" if i % 2 == 0 else "仓山区",
                    address="测试",
                    status="operating" if i % 2 == 0 else "pending",
                    connectors=1,
                    power_kw=10,
                )
                db.add(s)
                db.flush()
                ids.append(s.id)
        with TestClient(app, client=("test-page-" + secrets.token_hex(6), 1)) as client:
            assert (
                client.post(
                    "/api/v1/auth/login", json={"username": names[0], "password": password}
                ).status_code
                == 200
            )
            base = "/api/v1/stations?q=" + prefix
            first = client.get(base + "&page_size=7").json()
            last = client.get(base + "&page_size=7&page=4").json()
            assert first["total"] == last["total"] == 25
            assert first["page_size"] == 7 and first["page"] == 1
            assert len(first["items"]) == 7 and len(last["items"]) == 4
            assert {s["id"] for s in first["items"]}.isdisjoint(s["id"] for s in last["items"])
            filtered = client.get(
                base + "&status=operating&district=鼓楼区&page_size=10&page=2"
            ).json()
            assert filtered["total"] == 13 and len(filtered["items"]) == 3
            assert client.get(base + f"&operator_id={foreign_operator}").json()["total"] == 0
            assert client.get(base + "&page=0").status_code == 422
            assert client.get(base + "&page_size=101").status_code == 422
    finally:
        with SessionLocal.begin() as db:
            db.execute(delete(Station).where(Station.id.in_(ids)))
