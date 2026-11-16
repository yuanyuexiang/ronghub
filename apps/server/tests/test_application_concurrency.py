"""Real PostgreSQL requests must commit only one competing workflow decision."""

import secrets
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.main import app
from app.models import ApprovalAction, Station, StationApplication


@pytest.mark.parametrize("decisions", [("approve", "approve"), ("approve", "reject")])
def test_parallel_review_only_commits_one_decision(accounts, staff, decisions):
    names, password, stations = accounts
    clients = [TestClient(app, client=("review-race-" + secrets.token_hex(8), 1)) for _ in range(3)]
    application_id = None
    try:
        headers = []
        for client, username in zip(clients, [names[0], staff[0][0], staff[0][0]]):
            result = client.post(
                "/api/v1/auth/login", json={"username": username, "password": password}
            )
            assert result.status_code == 200
            headers.append({"x-csrf-token": result.json()["csrf_token"]})
        owner = clients[0]
        created = owner.post(
            "/api/v1/applications",
            headers=headers[0],
            json={"station_id": stations[0], "kind": "access"},
        )
        assert created.status_code == 201
        record = created.json()
        application_id = record["id"]
        url = f"/api/v1/applications/{record['id']}/actions"
        submitted = owner.post(url, headers=headers[0], json={"action": "submit", "version": 1})
        assert submitted.status_code == 200
        barrier = Barrier(2)

        def review(index):
            barrier.wait(timeout=10)
            return clients[index + 1].post(
                url,
                headers=headers[index + 1],
                json={
                    "action": decisions[index],
                    "version": 2,
                    "comment": "并发评审演示",
                },
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(review, range(2)))
        assert sorted(result.status_code for result in results) == [200, 409]
        winning_index = next(
            index for index, result in enumerate(results) if result.status_code == 200
        )
        decision = decisions[winning_index]
        with SessionLocal() as db:
            application = db.get(StationApplication, record["id"])
            assert application.version == 3
            assert application.status == ("approved" if decision == "approve" else "rejected")
            assert db.get(Station, stations[0]).status == (
                "access_approved" if decision == "approve" else "pending"
            )
            actions = list(
                db.scalars(
                    select(ApprovalAction.action)
                    .where(ApprovalAction.application_id == application.id)
                    .order_by(ApprovalAction.id)
                )
            )
            assert actions == ["create", "submit", decision]
    finally:
        if application_id is not None:
            with SessionLocal.begin() as db:
                db.execute(
                    delete(ApprovalAction).where(ApprovalAction.application_id == application_id)
                )
        for client in clients:
            client.close()
