import copy
import json
import secrets
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, func, select

from app.db import SessionLocal
from app.main import app
from app.models import (
    Audit,
    EnergyAllocation,
    EnergyRecord,
    Station,
    SubsidyAction,
    SubsidyAnnualBatch,
    SubsidyClaim,
    SubsidyPolicy,
)
from app.subsidy_rules import DeviceSubsidyRules


def rules():
    return {
        "configuration_version": 1,
        "swap_rate": "0.200001",
        "generation_period": "day",
        "star_coefficients": {str(star): str(star / 10 + 1) for star in range(6)},
        "fault_rate_below": "10.2500",
        "offline_rate_below": "20.0000",
    }


@pytest.mark.parametrize(
    "key,value",
    [
        ("configuration_version", True),
        ("configuration_version", 2),
        ("swap_rate", "NaN"),
        ("swap_rate", "-0.1"),
        ("swap_rate", "0.0000001"),
        ("generation_period", "week"),
        ("fault_rate_below", "100.0001"),
        ("offline_rate_below", None),
        ("star_coefficients", {"0": "1"}),
        ("star_coefficients", {str(i): "Infinity" for i in range(6)}),
    ],
)
def test_complete_bounded_configuration_rejects_unknown_or_ambiguous_values(key, value):
    with pytest.raises(ValidationError):
        DeviceSubsidyRules.model_validate({**rules(), key: value})


def test_device_policy_is_immutable_round_trips_and_cannot_fall_back_to_station_money(
    accounts, staff
):
    names, password, stations = accounts
    clients = [
        TestClient(app, client=("subsidy-rules-" + secrets.token_hex(6), 1)) for _ in range(3)
    ]
    reg, op, assoc = clients
    policies = []
    claim_id = None
    energy_id = None
    try:
        headers = []
        for client, name in zip(clients, [staff[0][0], names[0], staff[0][1]], strict=True):
            login = client.post("/api/v1/auth/login", json={"username": name, "password": password})
            assert login.status_code == 200
            headers.append({"x-csrf-token": login.json()["csrf_token"]})
        body = {
            "code": "RULES-" + secrets.token_hex(8),
            "version": 1,
            "name": "设备配置",
            "rate": "0.100001",
            "effective_from": "2026-01-01",
            "effective_to": "2026-12-31",
            "device_rules": rules(),
        }
        assert op.post("/api/v1/subsidy/policies", headers=headers[1], json=body).status_code == 403
        assert (
            assoc.post("/api/v1/subsidy/policies", headers=headers[2], json=body).status_code == 403
        )
        created = reg.post("/api/v1/subsidy/policies", headers=headers[0], json=body)
        assert created.status_code == 201, created.text
        first = created.json()
        policies.append(first["id"])
        assert not first["claim_supported"] and first["calculation_basis"] == "device_rules_pending"
        assert (
            first["device_rules"]["swap_rate"] == "0.200001"
            and first["device_rules"]["fault_rate_below"] == "10.2500"
        )
        assert (
            reg.post("/api/v1/subsidy/policies", headers=headers[0], json=body).status_code == 409
        )
        changed = copy.deepcopy(body)
        changed["version"] = 2
        changed["device_rules"]["star_coefficients"]["5"] = "2.123456"
        changed["device_rules"]["generation_period"] = "month"
        second = reg.post("/api/v1/subsidy/policies", headers=headers[0], json=changed)
        assert second.status_code == 201
        policies.append(second.json()["id"])
        old = reg.post(
            "/api/v1/subsidy/policies",
            headers=headers[0],
            json={**body, "code": body["code"] + "-OLD", "device_rules": None},
        )
        assert old.status_code == 201
        policies.append(old.json()["id"])
        assert old.json()["device_rules"] is None and old.json()["claim_supported"]
        listed = op.get("/api/v1/subsidy/policies").json()["items"]
        assert next(item for item in listed if item["id"] == first["id"]) == first
        with SessionLocal.begin() as db:
            db.get(Station, stations[0]).status = "operating"
            energy = EnergyRecord(
                station_id=stations[0],
                source="rule-test",
                external_id=secrets.token_hex(8),
                day=date(2026, 11, 1),
                kwh="25",
                eligible=True,
            )
            db.add(energy)
            db.flush()
            energy_id = energy.id
            before = db.scalar(select(func.count()).select_from(SubsidyClaim))
            batches = db.scalar(select(func.count()).select_from(SubsidyAnnualBatch))
            assert db.get(SubsidyPolicy, policies[-1]).device_rules is None
        request = {
            "station_id": stations[0],
            "policy_id": first["id"],
            "start_date": "2026-11-01",
            "end_date": "2026-11-01",
        }
        blocked = op.post("/api/v1/subsidy/claims", headers=headers[1], json=request)
        assert blocked.status_code == 409 and "未套用旧站级算法" in blocked.text
        annual = op.post(
            "/api/v1/subsidy/annual-batches",
            headers=headers[1],
            data={
                "payload": json.dumps(
                    {
                        "request_key": str(uuid.uuid4()),
                        "application_year": 2026,
                        "station_ids": [stations[0]],
                        "policy_id": first["id"],
                        "mode": "draft",
                    }
                )
            },
        )
        assert annual.status_code == 409, annual.text
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(SubsidyClaim)) == before
            assert db.scalar(select(func.count()).select_from(SubsidyAnnualBatch)) == batches
            assert db.get(EnergyAllocation, energy_id) is None
        legacy = op.post(
            "/api/v1/subsidy/claims",
            headers=headers[1],
            json={**request, "policy_id": policies[-1]},
        )
        assert legacy.status_code == 201, legacy.text
        claim_id = legacy.json()["id"]
        assert legacy.json()["base_amount"] == "2.50"
    finally:
        for client in clients:
            client.close()
        with SessionLocal.begin() as db:
            if claim_id:
                db.execute(delete(EnergyAllocation).where(EnergyAllocation.claim_id == claim_id))
                db.execute(delete(SubsidyAction).where(SubsidyAction.claim_id == claim_id))
                db.execute(delete(SubsidyClaim).where(SubsidyClaim.id == claim_id))
            if energy_id:
                db.execute(delete(EnergyRecord).where(EnergyRecord.id == energy_id))
            db.execute(
                delete(Audit).where(
                    Audit.action == "subsidy.policy.create", Audit.object_id.in_(policies)
                )
            )
            db.execute(delete(SubsidyPolicy).where(SubsidyPolicy.id.in_(policies)))
