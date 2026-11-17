import secrets
from pathlib import Path

from fastapi.testclient import TestClient

from app.contracts import (
    CurrentUser,
    EquipmentList,
    ErrorResponse,
    LoginResponse,
    StationDetail,
    StationPage,
)
from app.db import engine
from app.export_contract import artifacts
from app.main import app


def test_export_matches_runtime_and_does_not_connect(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Schema generation must not connect to the database")

    monkeypatch.setattr(engine, "connect", forbidden)
    generated = artifacts()
    root = Path(__file__).resolve().parents[3]
    for relative, content in generated.items():
        assert (root / relative).read_text() == content
    schema = app.openapi()
    paths = schema["paths"]
    assert paths["/api/v1/auth/login"]["post"]["security"] == []
    assert paths["/api/v1/auth/me"]["get"]["security"] == [{"BusinessCookie": []}]
    assert paths["/api/v1/auth/logout"]["post"]["security"] == [
        {"BusinessCookie": [], "CsrfHeader": []}
    ]
    assert paths["/api/v1/enrollment/me"]["get"]["security"] == [{"EnrollmentCookie": []}]
    assert paths["/api/v1/enrollment/me/submit"]["post"]["security"] == [
        {"EnrollmentCookie": [], "EnrollmentCsrfHeader": []}
    ]
    assert (
        schema["components"]["securitySchemes"]["EnrollmentCsrfHeader"]["name"]
        == "x-enrollment-csrf"
    )
    assert paths["/api/v1/enrollment/me/attachments"]["get"]["security"] == [
        {"EnrollmentCookie": []}
    ]
    assert paths["/api/v1/auth/staff-enrollment/me"]["get"]["security"] == [
        {"StaffApplicantCookie": []}
    ]
    assert paths["/api/v1/staff-applications"]["get"]["x-capability"] == "accounts.manage"
    assert paths["/api/v1/audits"]["get"]["x-capability"] == "audits.read"
    assert paths["/api/v1/stations"]["get"]["x-response-contract"] == "validated"
    assert paths["/api/v1/dashboard"]["get"]["x-response-contract"] == "validated"
    for path in paths.values():
        for operation in path.values():
            assert "x-response-contract" in operation
            assert operation["responses"]["422"]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorResponse"
            }


def test_public_shapes_match_real_pg_responses(accounts):
    names, password, stations = accounts
    with TestClient(app, client=("contract-" + secrets.token_hex(8), 1)) as client:
        result = client.post(
            "/api/v1/auth/login", json={"username": names[0], "password": password}
        )
        assert result.status_code == 200
        login = LoginResponse.model_validate(result.json())
        actor = CurrentUser.model_validate(client.get("/api/v1/auth/me").json())
        assert actor.csrf_token == login.csrf_token
        assert "password" not in CurrentUser.model_fields
        page = StationPage.model_validate(client.get("/api/v1/stations").json())
        assert [row.id for row in page.items] == [stations[0]]
        assert isinstance(page.items[0].power_kw, str)
        detail = StationDetail.model_validate(client.get(f"/api/v1/stations/{stations[0]}").json())
        assert detail.longitude is None and detail.coordinate_system == "unknown"
        assert (
            EquipmentList.model_validate(
                client.get(f"/api/v1/stations/{stations[0]}/equipment").json()
            ).items
            == []
        )
        invalid = client.get("/api/v1/stations", params={"page": 0})
        assert invalid.status_code == 422
        error = ErrorResponse.model_validate(invalid.json())
        assert isinstance(error.detail, list)
        assert "input" not in invalid.text
        assert client.post(
            "/api/v1/auth/logout", headers={"x-csrf-token": login.csrf_token}
        ).json() == {"ok": True}
