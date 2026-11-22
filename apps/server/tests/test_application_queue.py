import secrets

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Operator, Station, StationApplication, User


def test_queue_reaches_old_records_combines_filters_and_scopes(accounts, staff):
    names, password, stations = accounts
    key = secrets.token_hex(6)
    own_ids = []
    with SessionLocal.begin() as db:
        owner = db.scalar(select(User).where(User.username == names[0]))
        foreign = db.scalar(select(User).where(User.username == names[1]))
        reg_id = db.scalar(select(User.id).where(User.username == staff[0][0]))
        db.get(Station, stations[0]).name = "队列%_" + key
        db.get(Station, stations[0]).district = "队列区"
        db.get(Station, stations[1]).name = "外部企业" + key
        db.get(Station, stations[1]).district = "其他区"
        db.get(Operator, owner.operator_id).name = "QueueAlpha-" + key
        for index in range(122):
            record = StationApplication(
                station_id=stations[0],
                operator_id=owner.operator_id,
                applicant_id=owner.id,
                kind="access" if index % 2 else "change",
                status="withdrawn",
                payload={"note": "历史已撤回"},
            )
            db.add(record)
            db.flush()
            own_ids.append(record.id)
        other = StationApplication(
            station_id=stations[1],
            operator_id=foreign.operator_id,
            applicant_id=foreign.id,
            kind="access",
            status="withdrawn",
            payload={},
        )
        db.add(other)
        db.flush()
        other_id = other.id
    clients = [TestClient(app, client=("queue-" + secrets.token_hex(8), 1)) for _ in range(3)]
    try:
        for client, username in zip(clients, [names[0], staff[0][0], staff[0][1]]):
            assert (
                client.post(
                    "/api/v1/auth/login", json={"username": username, "password": password}
                ).status_code
                == 200
            )
        operator, regulator, association = clients
        response = operator.get("/api/v1/applications")
        assert response.status_code == 200
        first = response.json()
        assert first["total"] == 122 and len(first["items"]) == 20
        assert first["items"][0]["station_name"] == "队列%_" + key
        assert first["items"][0]["operator_name"] == "QueueAlpha-" + key
        collected = []
        for page in range(1, 8):
            data = operator.get("/api/v1/applications", params={"page": page}).json()
            collected.extend(row["id"] for row in data["items"])
        assert collected == list(reversed(own_ids))
        assert (
            operator.get("/api/v1/applications", params={"page": 2, "page_size": 100}).json()[
                "items"
            ][-1]["id"]
            == own_ids[0]
        )
        assert operator.get("/api/v1/applications", params={"page": 8}).json()["items"] == []
        filtered = operator.get(
            "/api/v1/applications",
            params={
                "q": "queuealpha-" + key,
                "kind": "change",
                "status": "withdrawn",
                "page_size": 100,
            },
        ).json()
        assert filtered["total"] == 61
        assert {row["id"] for row in filtered["items"]} == set(own_ids[::2])
        assert regulator.get("/api/v1/applications", params={"q": "%_"}).json()["total"] == 122
        assert (
            operator.get("/api/v1/applications", params={"application_id": other_id}).json()[
                "total"
            ]
            == 0
        )
        assert (
            operator.get("/api/v1/applications", params={"q": "外部企业" + key}).json()["total"]
            == 0
        )
        assert association.get("/api/v1/applications").status_code == 403
        for invalid in [
            {"page": 0},
            {"page_size": 101},
            {"status": "unknown"},
            {"kind": "unknown"},
            {"application_id": 0},
            {"application_id": 2**63},
            {"page": 2**63},
        ]:
            assert operator.get("/api/v1/applications", params=invalid).status_code == 422
        with SessionLocal.begin() as db:
            db.get(User, reg_id).allowed_districts = ["队列区"]
        assert (
            regulator.get("/api/v1/applications", params={"application_id": own_ids[0]}).json()[
                "total"
            ]
            == 1
        )
        assert (
            regulator.get("/api/v1/applications", params={"application_id": other_id}).json()[
                "total"
            ]
            == 0
        )
        assert regulator.get(f"/api/v1/applications/{other_id}").status_code == 404
    finally:
        for client in clients:
            client.close()
        with SessionLocal.begin() as db:
            db.get(User, reg_id).allowed_districts = None


def test_latest_real_submission_material_count_and_scoped_export(accounts):
    from datetime import UTC, datetime

    from app.models import ApprovalAction, Attachment

    names, password, stations = accounts
    with SessionLocal.begin() as db:
        owner, foreign = (db.scalar(select(User).where(User.username == name)) for name in names)
        station = db.get(Station, stations[0])
        station.name = "=Queue-export-" + secrets.token_hex(6)
        address = station.address
        entries = [
            StationApplication(
                station_id=sid,
                operator_id=user.operator_id,
                applicant_id=user.id,
                kind="access",
                status=status,
            )
            for sid, user, status in [
                (stations[0], owner, "draft"),
                (stations[0], owner, "submitted"),
                (stations[1], foreign, "submitted"),
            ]
        ]
        db.add_all(entries)
        db.flush()
        draft, submitted, outside = [entry.id for entry in entries]
        db.add_all(
            [
                ApprovalAction(
                    application_id=submitted,
                    actor_id=owner.id,
                    action="submit",
                    created_at=datetime(2026, 11, 20, 16, tzinfo=UTC),
                ),
                ApprovalAction(
                    application_id=submitted,
                    actor_id=owner.id,
                    action="submit",
                    created_at=datetime(2026, 11, 19, 16, tzinfo=UTC),
                ),
                *[
                    Attachment(
                        application_id=submitted,
                        uploaded_by=owner.id,
                        storage_key=secrets.token_hex(16),
                        filename="proof.pdf",
                        content_type="application/pdf",
                        size=10,
                        sha256="0" * 64,
                        active=active,
                    )
                    for active in [True, False]
                ],
            ]
        )
        name = station.name
    with TestClient(app, client=("app-submission-" + secrets.token_hex(8), 1)) as client:
        assert (
            client.post(
                "/api/v1/auth/login", json={"username": names[0], "password": password}
            ).status_code
            == 200
        )
        path = "/api/v1/applications"
        rows = {row["id"]: row for row in client.get(path).json()["items"]}
        assert rows[draft]["last_submitted_at"] is None
        assert rows[draft]["active_material_count"] == 0
        assert rows[submitted]["last_submitted_at"] == "2026-11-19T16:00:00+00:00"
        assert rows[submitted]["active_material_count"] == 1
        assert rows[submitted]["station_address"] == address
        assert outside not in rows
        window = {"submitted_from": "2026-11-20", "submitted_to": "2026-11-20"}
        filtered = client.get(path, params=window)
        assert filtered.status_code == 200, filtered.text
        assert [row["id"] for row in filtered.json()["items"]] == [submitted]
        assert (
            client.get(path, params={**window, "submitted_from": "2026-11-21"}).status_code == 422
        )
        exported = client.get(path + "/export", params=window)
        assert exported.status_code == 200, exported.text
        assert f"APP-{submitted}," in exported.text and f"'{name}" in exported.text
        assert f"APP-{draft}," not in exported.text and f"APP-{outside}," not in exported.text
