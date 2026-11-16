from fastapi.testclient import TestClient

from app.main import app


def test_isolation_csrf_and_logout(accounts):
    names, password, stations = accounts
    with TestClient(app) as c:
        assert c.get("/api/v1/stations").status_code == 401
        assert (
            c.post(
                "/api/v1/auth/login", json={"username": names[0], "password": "wrong"}
            ).status_code
            == 401
        )
        r = c.post("/api/v1/auth/login", json={"username": names[0], "password": password})
        assert r.status_code == 200
        csrf = r.json()["csrf_token"]
        assert c.get(f"/api/v1/stations/{stations[0]}").status_code == 200
        assert c.get(f"/api/v1/stations/{stations[1]}").status_code == 404
        assert {s["operator_id"] for s in c.get("/api/v1/stations").json()["items"]}.__len__() == 1
        assert c.post("/api/v1/auth/logout").status_code == 403
        assert c.post("/api/v1/auth/logout", headers={"x-csrf-token": csrf}).status_code == 200
        assert c.get("/api/v1/auth/me").status_code == 401
