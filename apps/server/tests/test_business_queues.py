import secrets
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.main import app
from app.models import (
    Operator,
    RatingApplication,
    RatingStandard,
    Station,
    SubsidyClaim,
    SubsidyPolicy,
    User,
)


def test_financial_and_rating_history_filters_paging_and_scope(accounts, staff):
    names, password, stations = accounts
    key = secrets.token_hex(8)
    with SessionLocal.begin() as db:
        owners = [db.scalar(select(User).where(User.username == name)) for name in names]
        reg_id = db.scalar(select(User.id).where(User.username == staff[0][0]))
        policy = SubsidyPolicy(
            code="queue-" + key,
            version=1,
            name="队列演示",
            rate="0.1",
            effective_from=date(2026, 1, 1),
            effective_to=date(2026, 12, 31),
        )
        standard = RatingStandard(code="queue-" + key, version=1, name="队列演示", rules={})
        db.add_all([policy, standard])
        db.flush()
        pid, sid = policy.id, standard.id
        expected = {"subsidy": [], "rating": []}
        foreign = {}
        for owner_index, count in [(0, 122), (1, 1)]:
            station = db.get(Station, stations[owner_index])
            station.name = ("队列%_" if owner_index == 0 else "外部队列") + key
            station.district = "本区" if owner_index == 0 else "其他区"
            station.status = "operating"
            db.get(Operator, owners[owner_index].operator_id).name = (
                "QueueALPHA-" if owner_index == 0 else "Foreign-"
            ) + key
            for _ in range(count):
                claim = SubsidyClaim(
                    station_id=station.id,
                    operator_id=owners[owner_index].operator_id,
                    policy_id=pid,
                    start_date=date(2026, 11, 1),
                    end_date=date(2026, 11, 30),
                    status="withdrawn",
                    total_kwh="42.005",
                    base_amount="4.20",
                    adjusted_amount="4.20",
                    snapshot={},
                )
                rating = RatingApplication(
                    station_id=station.id,
                    operator_id=owners[owner_index].operator_id,
                    standard_id=sid,
                    status="withdrawn",
                    kind="initial",
                    initial_scores={},
                    current_scores={},
                    calculation={"total": "90.00", "stars": 1, "missing": []},
                    rule_snapshot={},
                )
                db.add_all([claim, rating])
                db.flush()
                for name, row in [("subsidy", claim), ("rating", rating)]:
                    if owner_index == 0:
                        expected[name].append(row.id)
                    else:
                        foreign[name] = row.id
    clients = [TestClient(app, client=("queue-" + secrets.token_hex(8), 1)) for _ in range(3)]
    try:
        for client, name in zip(clients, [names[0], staff[0][0], staff[0][1]]):
            assert (
                client.post(
                    "/api/v1/auth/login", json={"username": name, "password": password}
                ).status_code
                == 200
            )
        owner, reg, association = clients
        for business, url in [
            ("subsidy", "/api/v1/subsidy/claims"),
            ("rating", "/api/v1/ratings/applications"),
        ]:
            first = owner.get(url).json()
            assert first["total"] == 122 and len(first["items"]) == 20
            assert first["items"][0]["operator_name"] == "QueueALPHA-" + key
            all_ids = []
            for page in range(1, 8):
                all_ids.extend(
                    row["id"] for row in owner.get(url, params={"page": page}).json()["items"]
                )
            assert all_ids == list(reversed(expected[business]))
            assert (
                owner.get(url, params={"page_size": 100, "page": 2}).json()["items"][-1]["id"]
                == expected[business][0]
            )
            assert (
                owner.get(url, params={"q": "queuealpha-" + key, "status": "withdrawn"}).json()[
                    "total"
                ]
                == 122
            )
            assert reg.get(url, params={"q": "%_" + key}).json()["total"] == 122
            assert owner.get(url, params={"record_id": foreign[business]}).json()["total"] == 0
            assert owner.get(url, params={"q": "外部队列" + key}).json()["total"] == 0
            assert owner.get(url, params={"status": "submitted"}).json()["total"] == 0
            for invalid in [
                {"page": 0},
                {"page_size": 101},
                {"status": "invalid"},
                {"record_id": 2**63},
            ]:
                assert owner.get(url, params=invalid).status_code == 422
            assert association.get(url).status_code == (403 if business == "subsidy" else 200)
        rating_url = "/api/v1/ratings/applications"
        assert owner.get(rating_url, params={"kind": "initial"}).json()["total"] == 122
        assert owner.get(rating_url, params={"kind": "renewal"}).json()["total"] == 0
        assert owner.get(rating_url, params={"kind": "invalid"}).status_code == 422
        with SessionLocal.begin() as db:
            db.get(User, reg_id).allowed_districts = ["本区"]
        for business, url in [("subsidy", "/api/v1/subsidy/claims"), ("rating", rating_url)]:
            assert reg.get(url, params={"record_id": foreign[business]}).json()["total"] == 0
            assert reg.get(url, params={"record_id": expected[business][0]}).json()["total"] == 1
    finally:
        for client in clients:
            client.close()
        with SessionLocal.begin() as db:
            db.get(User, reg_id).allowed_districts = None
            db.execute(delete(SubsidyClaim).where(SubsidyClaim.policy_id == pid))
            db.execute(delete(RatingApplication).where(RatingApplication.standard_id == sid))
            db.execute(delete(SubsidyPolicy).where(SubsidyPolicy.id == pid))
            db.execute(delete(RatingStandard).where(RatingStandard.id == sid))
