import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.config import settings
from app.db import SessionLocal
from app.enrollment_contracts import EnrollmentRecord, EnrollmentSessionResponse
from app.main import app
from app.models import (
    Audit,
    EnrollmentSession,
    LoginSession,
    Operator,
    Registration,
    RegistrationAction,
    RegistrationAttachment,
    RoleProfile,
    User,
)


@pytest.fixture()
def case(accounts, staff, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    clients = [
        TestClient(
            app,
            client=("enroll-contract-" + secrets.token_hex(6), 1),
            raise_server_exceptions=False,
        )
        for _ in range(2)
    ]
    tag = secrets.token_hex(10)
    body = {
        "username": "contract-" + tag,
        "password": accounts[1],
        "company_name": "Contract-" + tag,
        "credit_code": "A" + secrets.token_hex(9).upper()[:17],
        "contacts": [{"name": "测试", "phone": "100001", "primary": True}],
    }
    applicant, reviewer = clients
    created = applicant.post("/api/v1/enrollment", json=body)
    assert created.status_code == 201, created.text
    record = created.json()
    rid = record["id"]
    auth = reviewer.post(
        "/api/v1/auth/login", json={"username": staff[0][0], "password": accounts[1]}
    )
    assert auth.status_code == 200
    with SessionLocal.begin() as db:
        profile = RoleProfile(
            name="enroll-contract-" + tag, base_role="operator", permissions=["stations.read"]
        )
        db.add(profile)
        db.flush()
        pid = profile.id
    try:
        yield {
            "applicant": applicant,
            "reviewer": reviewer,
            "body": body,
            "id": rid,
            "record": record,
            "eh": {"x-enrollment-csrf": record["csrf_token"]},
            "rh": {"x-csrf-token": auth.json()["csrf_token"]},
            "profile": pid,
            "files": tmp_path,
        }
    finally:
        for client in clients:
            client.close()
        with SessionLocal.begin() as db:
            r = db.get(Registration, rid)
            uid, oid = (r.user_id, r.operator_id) if r else (None, None)
            db.execute(delete(EnrollmentSession).where(EnrollmentSession.registration_id == rid))
            db.execute(
                delete(RegistrationAttachment).where(RegistrationAttachment.registration_id == rid)
            )
            db.execute(delete(RegistrationAction).where(RegistrationAction.registration_id == rid))
            db.execute(
                delete(Audit).where(Audit.action.like("registration.%"), Audit.object_id == rid)
            )
            db.execute(delete(Registration).where(Registration.id == rid))
            if uid:
                db.execute(delete(LoginSession).where(LoginSession.user_id == uid))
                db.execute(delete(User).where(User.id == uid))
            if oid:
                db.execute(delete(Operator).where(Operator.id == oid))
            db.execute(delete(RoleProfile).where(RoleProfile.id == pid))


def upload(c):
    return c["applicant"].post(
        "/api/v1/enrollment/me/attachments",
        headers=c["eh"],
        files={"file": ("proof.pdf", b"%PDF-1.4\nSynthetic", "application/pdf")},
    )


def fail(value):
    raise RuntimeError("Response projection failed before commit")


def test_create_projection_failure_leaves_no_registration_session_or_action(accounts, monkeypatch):
    tag = secrets.token_hex(10)
    with monkeypatch.context() as patch:
        patch.setattr(EnrollmentSessionResponse, "model_validate", fail)
        with TestClient(app, raise_server_exceptions=False, client=(tag, 1)) as client:
            result = client.post(
                "/api/v1/enrollment",
                json={
                    "username": tag,
                    "password": accounts[1],
                    "company_name": tag,
                    "credit_code": "A" + secrets.token_hex(9).upper()[:17],
                    "contacts": [{"name": "测试", "phone": "1"}],
                },
            )
            assert result.status_code == 500
    with SessionLocal() as db:
        assert db.scalar(select(Registration.id).where(Registration.username == tag)) is None


@pytest.mark.parametrize("action", ["edit", "submit", "approve"])
def test_projection_failure_rolls_back_profile_material_versions_and_new_account(
    case, monkeypatch, action
):
    c = case
    assert upload(c).status_code == 201
    if action == "approve":
        result = c["applicant"].post(
            "/api/v1/enrollment/me/submit", headers=c["eh"], json={"version": 1}
        )
        assert result.status_code == 200
    with SessionLocal() as db:
        old = db.get(Registration, c["id"])
        version, status = old.version, old.status
        actions = db.scalar(
            select(func.count())
            .select_from(RegistrationAction)
            .where(RegistrationAction.registration_id == c["id"])
        )
    with monkeypatch.context() as patch:
        patch.setattr(EnrollmentRecord, "model_validate", fail)
        if action == "edit":
            result = c["applicant"].patch(
                "/api/v1/enrollment/me",
                headers=c["eh"],
                json={
                    **{k: v for k, v in c["body"].items() if k not in {"username", "password"}},
                    "company_name": "Changed",
                    "version": version,
                },
            )
        elif action == "submit":
            result = c["applicant"].post(
                "/api/v1/enrollment/me/submit", headers=c["eh"], json={"version": version}
            )
        else:
            result = c["reviewer"].post(
                f"/api/v1/registrations/{c['id']}/review",
                headers=c["rh"],
                json={
                    "version": version,
                    "action": "approve",
                    "comment": "核验测试",
                    "role_profile_id": c["profile"],
                    "role_profile_version": 1,
                },
            )
        assert result.status_code == 500, result.text
    with SessionLocal() as db:
        r = db.get(Registration, c["id"])
        assert (r.version, r.status, r.company_name) == (version, status, c["body"]["company_name"])
        assert (
            db.scalar(
                select(func.count())
                .select_from(RegistrationAction)
                .where(RegistrationAction.registration_id == r.id)
            )
            == actions
        )
        assert db.scalar(select(User.id).where(User.username == c["body"]["username"])) is None
        assert (
            db.scalar(select(Operator.id).where(Operator.credit_code == c["body"]["credit_code"]))
            is None
        )
        a = db.scalar(
            select(RegistrationAttachment).where(RegistrationAttachment.registration_id == r.id)
        )
        assert a.submitted_version == (version if action == "approve" else None)


def test_material_projection_failure_cleans_file_and_row(case, monkeypatch):
    from app.material_contracts import ApplicationMaterialItem

    c = case
    before = set(c["files"].iterdir())
    with monkeypatch.context() as patch:
        patch.setattr(ApplicationMaterialItem, "model_validate", fail)
        assert upload(c).status_code == 500
    assert set(c["files"].iterdir()) == before
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(RegistrationAttachment.id).where(
                    RegistrationAttachment.registration_id == c["id"]
                )
            )
            is None
        )


def test_typed_reads_are_private_uncached_and_literal_search_does_not_expand(case):
    c = case
    assert c["record"]["operator_id"] is None and c["record"]["user_id"] is None
    assert upload(c).status_code == 201
    assert (
        c["applicant"]
        .post("/api/v1/enrollment/me/submit", headers=c["eh"], json={"version": 1})
        .status_code
        == 200
    )
    for client, path in [
        (c["applicant"], "/api/v1/enrollment/me"),
        (c["applicant"], "/api/v1/enrollment/me/attachments"),
        (c["reviewer"], "/api/v1/registrations"),
        (c["reviewer"], "/api/v1/registrations/options"),
        (c["reviewer"], f"/api/v1/registrations/{c['id']}"),
    ]:
        response = client.get(path)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert "password_hash" not in response.text and "storage_key" not in response.text
    assert (
        c["reviewer"]
        .get("/api/v1/registrations", params={"q": c["body"]["company_name"] + "%"})
        .json()["total"]
        == 0
    )
    assert c["reviewer"].get("/api/v1/registrations?page=2147483648").status_code == 422
    assert c["applicant"].get(f"/api/v1/registrations/{c['id']}").status_code == 401
    mid = c["applicant"].get("/api/v1/enrollment/me/attachments").json()["items"][0]["id"]
    response = c["applicant"].get(f"/api/v1/enrollment/me/attachments/{mid}/download")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith("application/pdf")


def test_registration_list_and_count_share_snapshot_during_real_applicant_edit(case):
    from sqlalchemy import event

    from app.db import engine

    c = case
    assert upload(c).status_code == 201
    assert (
        c["applicant"]
        .post("/api/v1/enrollment/me/submit", headers=c["eh"], json={"version": 1})
        .status_code
        == 200
    )
    assert (
        c["reviewer"]
        .post(
            f"/api/v1/registrations/{c['id']}/review",
            headers=c["rh"],
            json={"version": 2, "action": "reject", "comment": "Allow applicant correction"},
        )
        .status_code
        == 200
    )
    fired = []
    renamed = "Renamed-" + secrets.token_hex(8)

    def edit_after_select(connection, cursor, statement, parameters, context, many):
        if (
            fired
            or "FROM registrations" not in statement
            or "ORDER BY registrations.id" not in statement
        ):
            return
        fired.append(True)
        result = c["applicant"].patch(
            "/api/v1/enrollment/me",
            headers=c["eh"],
            json={
                **{k: v for k, v in c["body"].items() if k not in {"username", "password"}},
                "company_name": renamed,
                "version": 3,
            },
        )
        assert result.status_code == 200, result.text

    event.listen(engine, "after_cursor_execute", edit_after_select)
    try:
        response = c["reviewer"].get(
            "/api/v1/registrations", params={"q": c["body"]["company_name"]}
        )
        assert response.status_code == 200, response.text
        assert fired and response.json()["total"] == 1
        assert response.json()["items"][0]["company_name"] == c["body"]["company_name"]
    finally:
        event.remove(engine, "after_cursor_execute", edit_after_select)
    with SessionLocal() as db:
        assert db.get(Registration, c["id"]).company_name == renamed
