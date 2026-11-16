import secrets
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.main import app
from app.models import InspectionTask, Station, User
from app.routers import inspections


def test_inspection_filter_time_boundary_and_scope(accounts, staff, monkeypatch):
    names, password, stations = accounts
    at = datetime(2026, 11, 15, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(inspections, "now", lambda: at)
    ids = []
    with SessionLocal.begin() as db:
        owners = [db.scalar(select(User).where(User.username == name)) for name in names]
        reg_id = db.scalar(select(User.id).where(User.username == staff[0][0]))
        db.get(Station, stations[0]).district = "本区"
        db.get(Station, stations[1]).district = "其他区"
        for i in range(122):
            task = InspectionTask(
                station_id=stations[0],
                operator_id=owners[0].operator_id,
                title="队列%_" + names[0],
                description="演示",
                severity="major",
                due_at=at - timedelta(seconds=1),
                created_by=owners[0].id,
            )
            db.add(task)
            db.flush()
            ids.append(task.id)
        for status, deadline, owner_index in [
            ("closed", at - timedelta(days=1), 0),
            ("assigned", at, 0),
            ("assigned", at + timedelta(days=1), 0),
            ("assigned", at - timedelta(days=1), 1),
        ]:
            db.add(
                InspectionTask(
                    station_id=stations[owner_index],
                    operator_id=owners[owner_index].operator_id,
                    title="边界" + names[0],
                    description="演示",
                    severity="normal",
                    due_at=deadline,
                    status=status,
                    created_by=owners[owner_index].id,
                )
            )
    clients = [
        TestClient(app, client=("inspection-query-" + secrets.token_hex(8), 1)) for _ in range(3)
    ]
    try:
        for client, name in zip(clients, [names[0], staff[0][0], staff[0][1]]):
            assert (
                client.post(
                    "/api/v1/auth/login", json={"username": name, "password": password}
                ).status_code
                == 200
            )
        owner, reg, association = clients
        url = "/api/v1/inspections"
        collected = []
        for page in range(1, 8):
            result = owner.get(
                url,
                params={
                    "q": "%_" + names[0],
                    "overdue": True,
                    "status": "assigned",
                    "severity": "major",
                    "page": page,
                },
            ).json()
            assert result["total"] == 122 and result["query_at"] == at.isoformat()
            assert all(row["overdue"] for row in result["items"])
            collected.extend(row["id"] for row in result["items"])
        assert collected == list(reversed(ids))
        assert owner.get(url, params={"overdue": False}).json()["total"] == 3
        assert owner.get(url, params={"status": "closed", "overdue": True}).json()["total"] == 0
        assert owner.get(url, params={"record_id": ids[0]}).json()["total"] == 1
        assert association.get(url).status_code == 403
        for params in [
            {"severity": "bad"},
            {"status": "bad"},
            {"overdue": "invalid"},
            {"page_size": 101},
            {"page": 0},
        ]:
            assert owner.get(url, params=params).status_code == 422
        with SessionLocal.begin() as db:
            db.get(User, reg_id).allowed_districts = ["本区"]
        assert reg.get(url, params={"q": names[0]}).json()["total"] == 125
    finally:
        for client in clients:
            client.close()
        with SessionLocal.begin() as db:
            db.get(User, reg_id).allowed_districts = None
            db.execute(delete(InspectionTask).where(InspectionTask.station_id.in_(stations)))
