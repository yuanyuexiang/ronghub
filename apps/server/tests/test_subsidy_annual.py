# ruff: noqa: F811 - imported pytest fixtures
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from test_business_exports import login
from test_subsidy import finance  # noqa: F401
from test_subsidy_evidence import headers, zipped

from app.config import settings
from app.db import SessionLocal
from app.models import (
    Audit,
    EnergyAllocation,
    EnergyRecord,
    Equipment,
    Station,
    SubsidyAction,
    SubsidyAnnualBatch,
    SubsidyAttachment,
    SubsidyClaim,
    User,
)
from app.routers import subsidy_annual as annual


@pytest.fixture
def annual_data(finance, accounts):
    uid, sid, pid = finance
    with SessionLocal.begin() as db:
        owner = db.get(User, uid)
        second = Station(
            operator_id=owner.operator_id,
            source="test-annual",
            external_id=str(uuid4()),
            name="第二个年度场站",
            district="测试",
            address="测试",
            power_kw=100,
            connectors=1,
            status="operating",
        )
        db.add(second)
        db.flush()
        second_id = second.id
        for station_id in [sid, second_id]:
            db.add(
                Equipment(
                    station_id=station_id, external_id=str(uuid4()), name="真实终端", power_kw=100
                )
            )
        db.add(
            EnergyRecord(
                station_id=second_id,
                source="test-annual",
                external_id=str(uuid4()),
                day=date(2026, 7, 1),
                kwh="42.005",
            )
        )
    yield uid, [sid, second_id], pid
    with SessionLocal.begin() as db:
        db.execute(delete(SubsidyAnnualBatch).where(SubsidyAnnualBatch.actor_id == uid))
        cids = select(SubsidyClaim.id).where(SubsidyClaim.policy_id == pid)
        db.execute(delete(SubsidyAttachment).where(SubsidyAttachment.claim_id.in_(cids)))
        db.execute(delete(SubsidyAction).where(SubsidyAction.claim_id.in_(cids)))
        db.execute(delete(EnergyAllocation).where(EnergyAllocation.claim_id.in_(cids)))
        db.execute(delete(SubsidyClaim).where(SubsidyClaim.policy_id == pid))
        db.execute(delete(EnergyRecord).where(EnergyRecord.station_id == second_id))
        db.execute(delete(Equipment).where(Equipment.station_id.in_([sid, second_id])))
        db.execute(delete(Station).where(Station.id == second_id))


def body(data, **changes):
    return {
        "request_key": str(uuid4()),
        "application_year": 2026,
        "station_ids": data[1],
        "policy_id": data[2],
        "application_note": "年度申报说明",
        "mode": "submit",
        **changes,
    }


def post(client, payload, attach=True, content=None):
    return client.post(
        "/api/v1/subsidy/annual-batches",
        headers=headers(client),
        data={"payload": json.dumps(payload)},
        files=[("files", ("年度.zip", zipped() if content is None else content, "application/zip"))]
        if attach
        else None,
    )


def counts(pid):
    with SessionLocal() as db:
        return tuple(
            db.scalar(select(func.count()).select_from(model).where(condition))
            for model, condition in [
                (SubsidyClaim, SubsidyClaim.policy_id == pid),
                (
                    EnergyAllocation,
                    EnergyAllocation.claim_id.in_(
                        select(SubsidyClaim.id).where(SubsidyClaim.policy_id == pid)
                    ),
                ),
            ]
        )


def test_annual_submit_creates_independent_frozen_claims_and_retry_receipt(annual_data, accounts):
    client = login(accounts[0][0], accounts[1])
    payload = body(annual_data)
    first = post(client, payload)
    assert first.status_code == 201, first.text
    result = first.json()
    assert result["replayed"] is False
    receipt = result["receipt"]
    claims = receipt["items"]
    assert len(claims) == 2 and {c["station_id"] for c in claims} == set(annual_data[1])
    assert all(c["status"] == "submitted" and c["version"] == 2 for c in claims)
    assert all(c["start_date"] == "2026-01-01" and c["end_date"] == "2026-12-31" for c in claims)
    materials = []
    for claim in claims:
        path = f"/api/v1/subsidy/claims/{claim['id']}"
        detail = client.get(path).json()
        assert detail["snapshot"]["application_year"] == 2026
        assert detail["snapshot"]["submission"]["note"] == payload["application_note"]
        item = client.get(path + "/attachments").json()["items"][0]
        assert item["submitted_version"] == 2
        downloaded = client.get(path + f"/attachments/{item['id']}/download")
        assert downloaded.content == zipped()
        materials.append(item["id"])
    assert len(set(materials)) == 2
    with SessionLocal() as db:
        keys = list(
            db.scalars(
                select(SubsidyAttachment.storage_key).where(SubsidyAttachment.id.in_(materials))
            )
        )
    assert len(set(keys)) == 2
    before_files = set(Path(settings.storage_path).iterdir())
    again = post(client, {**payload, "station_ids": list(reversed(payload["station_ids"]))})
    assert again.status_code == 201 and again.json() == {"replayed": True, "receipt": receipt}
    assert set(Path(settings.storage_path).iterdir()) == before_files
    assert post(client, {**payload, "application_note": "不同说明"}).status_code == 409
    assert counts(annual_data[2]) == (2, 3)
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(SubsidyAnnualBatch)
                .where(SubsidyAnnualBatch.actor_id == annual_data[0])
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(Audit)
                .where(Audit.user_id == annual_data[0], Audit.action == "subsidy.annual_create")
            )
            == 1
        )
    # A retry receipt deliberately keeps creation state even after later withdrawal.
    client.post(
        f"/api/v1/subsidy/claims/{claims[0]['id']}/actions",
        headers=headers(client),
        json={"action": "withdraw", "version": 2},
    )
    assert post(client, payload).json()["receipt"] == receipt
    client.close()


def test_late_station_error_rolls_back_claims_allocations_files_and_audit(annual_data, accounts):
    with SessionLocal.begin() as db:
        db.execute(delete(EnergyRecord).where(EnergyRecord.station_id == annual_data[1][1]))
    client = login(accounts[0][0], accounts[1])
    result = post(client, body(annual_data))
    assert result.status_code == 409, result.text
    assert counts(annual_data[2]) == (0, 0)
    assert list(Path(settings.storage_path).iterdir()) == []
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(Audit)
                .where(Audit.user_id == annual_data[0], Audit.action.like("subsidy.%"))
            )
            == 0
        )
    client.close()


@pytest.mark.parametrize(
    "changes, attach, expected",
    [
        ({"station_ids": []}, True, 422),
        ({"application_year": True}, True, 422),
        ({"application_year": "2026"}, True, 422),
        ({"application_year": 9999}, True, 422),
        ({"mode": "approve"}, True, 422),
        ({"application_note": " "}, True, 422),
        ({}, False, 422),
        ({"extra": 1}, True, 422),
        ({"station_ids": [2147483648]}, True, 422),
    ],
)
def test_annual_input_boundaries(annual_data, accounts, changes, attach, expected):
    client = login(accounts[0][0], accounts[1])
    result = post(client, body(annual_data, **changes), attach=attach)
    assert result.status_code == expected, result.text
    assert counts(annual_data[2]) == (0, 0)
    client.close()


def test_draft_can_be_incomplete_and_isolation_eligibility_remain_server_side(
    annual_data, accounts
):
    client = login(accounts[0][0], accounts[1])
    assert (
        post(client, body(annual_data, station_ids=[*annual_data[1], accounts[2][1]])).status_code
        == 404
    )
    assert post(client, body(annual_data, station_ids=[annual_data[1][0]] * 2)).status_code == 422
    with SessionLocal.begin() as db:
        db.execute(delete(Equipment).where(Equipment.station_id == annual_data[1][1]))
    assert post(client, body(annual_data)).status_code == 409
    created = post(
        client,
        body(annual_data, station_ids=[annual_data[1][0]], mode="draft", application_note=""),
        attach=False,
    )
    assert created.status_code == 201, created.text
    assert created.json()["receipt"]["items"][0]["status"] == "draft"
    client.close()


def test_annual_station_picker_counts_real_equipment_and_marks_unavailable(annual_data, accounts):
    with SessionLocal.begin() as db:
        db.get(Station, annual_data[1][0]).status = "access_approved"
        db.execute(delete(Equipment).where(Equipment.station_id == annual_data[1][1]))
    client = login(accounts[0][0], accounts[1])
    result = client.get("/api/v1/subsidy/annual-stations", params={"page_size": 1}).json()
    assert result["total"] == 2 and result["default_year"] == annual.current_year() - 1
    first = result["items"][0]
    assert first["equipment_count"] == 1 and first["equipment_power_kw"] == "100.00"
    assert not first["selectable"] and "已投运" in first["unavailable_reason"]
    second = client.get("/api/v1/subsidy/annual-stations?page=2&page_size=1").json()["items"][0]
    assert second["equipment_count"] == 0 and second["equipment_power_kw"] is None
    assert not second["selectable"] and "无终端" in second["unavailable_reason"]
    assert client.get("/api/v1/subsidy/annual-stations", params={"q": "%"}).json()["total"] == 0
    client.close()


def test_projection_failure_cleans_all_created_evidence_and_database(
    annual_data, accounts, monkeypatch
):
    client = login(accounts[0][0], accounts[1])

    def fail(*args, **kwargs):
        raise ValidationError.from_exception_data("SubsidyAnnualResult", [])

    monkeypatch.setattr(annual.SubsidyAnnualResult, "model_validate", fail)
    with pytest.raises(ValidationError):
        post(client, body(annual_data))
    assert counts(annual_data[2]) == (0, 0)
    assert list(Path(settings.storage_path).iterdir()) == []
    client.close()


def test_simultaneous_identical_retry_creates_one_batch(annual_data, accounts, monkeypatch):
    clients = [login(accounts[0][0], accounts[1]) for _ in range(2)]
    payload = body(annual_data)
    barrier = Barrier(2)
    authorize = annual.authorize_business_write

    def wait_before_lock(*args):
        barrier.wait(timeout=15)
        return authorize(*args)

    monkeypatch.setattr(annual, "authorize_business_write", wait_before_lock)
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda client: post(client, payload), clients))
    assert [r.status_code for r in responses] == [201, 201]
    assert sorted(r.json()["replayed"] for r in responses) == [False, True]
    assert responses[0].json()["receipt"] == responses[1].json()["receipt"]
    assert counts(annual_data[2]) == (2, 3)
    for client in clients:
        client.close()


def test_permissions_require_read_create_and_conditional_material_submit(annual_data, accounts):
    from app.models import RoleProfile

    uid = annual_data[0]
    with SessionLocal.begin() as db:
        profile = RoleProfile(
            name="annual-" + str(uuid4()),
            base_role="operator",
            permissions=["subsidy.read", "subsidy.create"],
        )
        db.add(profile)
        db.flush()
        profile_id = profile.id
        db.get(User, uid).role_profile_id = profile_id
    client = login(accounts[0][0], accounts[1])
    try:
        payload = body(annual_data)
        assert post(client, payload).status_code == 403  # Materials required independently.
        with SessionLocal.begin() as db:
            db.get(RoleProfile, profile_id).permissions = [
                "subsidy.read",
                "subsidy.create",
                "subsidy.materials",
            ]
        assert post(client, payload).status_code == 403  # Submit is not implied.
        draft_payload = body(annual_data, mode="draft", application_note="")
        assert post(client, draft_payload, attach=False).status_code == 201
        with SessionLocal.begin() as db:
            db.get(RoleProfile, profile_id).permissions = [
                "subsidy.create",
                "subsidy.materials",
                "subsidy.submit",
            ]
        assert post(client, draft_payload, attach=False).status_code == 403  # Replay rechecks read.
        with SessionLocal.begin() as db:
            db.get(RoleProfile, profile_id).permissions = ["subsidy.read", "subsidy.create"]
            db.get(User, uid).allowed_districts = []
        assert (
            post(client, draft_payload, attach=False).status_code == 404
        )  # Replay rechecks scope.
    finally:
        client.close()
        with SessionLocal.begin() as db:
            db.get(User, uid).role_profile_id = None
            db.delete(db.get(RoleProfile, profile_id))


def test_year_coverage_bad_zip_and_batch_byte_limits_are_atomic(annual_data, accounts, monkeypatch):
    client = login(accounts[0][0], accounts[1])
    assert post(client, body(annual_data, application_year=2025)).status_code == 422
    assert post(client, body(annual_data), content=b"%PDF-invalid").status_code == 415
    monkeypatch.setattr(annual, "MAX_BATCH_BYTES", len(zipped()) * 2 - 1)
    assert post(client, body(annual_data)).status_code == 413
    assert counts(annual_data[2]) == (0, 0)
    assert list(Path(settings.storage_path).iterdir()) == []
    client.close()


def test_opposite_station_order_competing_new_requests_cannot_double_allocate(
    annual_data, accounts, monkeypatch
):
    clients = [login(accounts[0][0], accounts[1]) for _ in range(2)]
    payloads = [body(annual_data), body(annual_data, station_ids=list(reversed(annual_data[1])))]
    authorize = annual.authorize_business_write
    barrier = Barrier(2)

    def wait_before_lock(*args):
        barrier.wait(timeout=15)
        return authorize(*args)

    monkeypatch.setattr(annual, "authorize_business_write", wait_before_lock)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda pair: post(*pair), zip(clients, payloads, strict=True)))
    assert sorted(result.status_code for result in results) == [201, 409]
    assert counts(annual_data[2]) == (2, 3)
    assert len(list(Path(settings.storage_path).iterdir())) == 2
    for client in clients:
        client.close()
