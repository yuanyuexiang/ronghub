"""Login must not conflict with business FK key-share locks in reverse order."""

import secrets
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.db import SessionLocal
from app.main import app
from app.models import ApprovalAction, Operator, StationApplication, User


def test_login_and_business_foreign_keys_do_not_deadlock(accounts):
    names, password, stations = accounts
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.username == names[0]))
        uid, oid = user.id, user.operator_id
        application = StationApplication(
            station_id=stations[0], operator_id=oid, applicant_id=uid, kind="access", payload={}
        )
        db.add(application)
        db.flush()
        aid = application.id
    client = TestClient(app, client=("lock-test-" + secrets.token_hex(8), 1))
    business = SessionLocal()
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        # This is the row lock acquired by a real operator FK insert, held until commit.
        business.execute(
            select(Operator.id).where(Operator.id == oid).with_for_update(read=True, key_share=True)
        )
        pending = pool.submit(
            client.post, "/api/v1/auth/login", json={"username": names[0], "password": password}
        )
        deadline = time.monotonic() + 5
        while True:
            with SessionLocal() as observer:
                waiting = observer.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE state='active' AND wait_event_type='Lock' AND query LIKE '%WHERE operators.id%' AND query LIKE '%FOR UPDATE%'"
                    )
                )
            if waiting:
                break
            assert not pending.done(), "Login unexpectedly skipped company locking"
            assert time.monotonic() < deadline, "Did not observe the intended company lock wait"
            time.sleep(0.02)
        # User FK key-share must remain compatible with the login's non-key update lock.
        business.add(
            ApprovalAction(
                application_id=aid, actor_id=uid, action="create", comment="并发外键演示"
            )
        )
        business.commit()
        assert pending.result(timeout=10).status_code == 200
    finally:
        business.rollback()
        business.close()
        pool.shutdown(wait=True, cancel_futures=True)
        client.close()
