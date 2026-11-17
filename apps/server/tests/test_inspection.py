from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

from app import inspection
from app.db import SessionLocal
from app.inspection_documents import ExecutionContact
from app.models import Audit, InspectionAction, InspectionAttachment, InspectionTask, User, now


def test_task_isolation_reply_review_and_overdue(accounts, tmp_path, monkeypatch):
    from io import BytesIO

    from starlette.datastructures import UploadFile

    from app.config import settings
    from app.storage import store_upload

    monkeypatch.setattr(settings, "storage_path", str(tmp_path))

    def submit(db, owner, task, version, comment):
        values, _ = store_upload(UploadFile(BytesIO(b"%PDF-1.4 Synthetic"), filename="proof.pdf"))
        db.add(InspectionAttachment(task_id=task.id, uploaded_by=owner.id, **values))
        db.flush()
        return inspection.act(
            db,
            owner,
            task.id,
            version,
            "reply",
            comment,
            contact=ExecutionContact(name="Executor", phone="13800000001"),
            performed_at=now() - timedelta(days=1),
        )

    names, _, stations = accounts
    task_id = None
    try:
        with SessionLocal.begin() as db:
            owner = db.scalar(select(User).where(User.username == names[0]))
            other = db.scalar(select(User).where(User.username == names[1]))
            reg = SimpleNamespace(id=owner.id, role="regulator")
            task = inspection.create(
                db, reg, stations[0], "测试巡检", "核查安全材料", "major", now() + timedelta(days=1)
            )
            task_id = task.id
            assert task.operator_id == owner.operator_id
            with pytest.raises(HTTPException) as e:
                inspection.access(db, other, task.id)
            assert e.value.status_code == 404
            with pytest.raises(HTTPException) as e:
                inspection.act(db, owner, task.id, 1, "close", "越权")
            assert e.value.status_code == 403
            submit(db, owner, task, 1, "已整改")
            with pytest.raises(HTTPException) as e:
                inspection.act(db, reg, task.id, 1, "close", "旧版本")
            assert e.value.status_code == 409
            inspection.act(db, reg, task.id, 2, "return", "请补充证据")
            task.due_at = now() - timedelta(seconds=1)
            assert inspection.output(task)["overdue"]
            submit(db, owner, task, 3, "补充说明")
            inspection.act(db, reg, task.id, 4, "close", "演示核验完成")
            assert not inspection.output(task)["overdue"]
            db.flush()
            assert (
                len(
                    list(
                        db.scalars(
                            select(InspectionAction).where(InspectionAction.task_id == task.id)
                        )
                    )
                )
                == 5
            )
    finally:
        if task_id:
            with SessionLocal.begin() as db:
                db.execute(
                    delete(InspectionAttachment).where(InspectionAttachment.task_id == task_id)
                )
                db.execute(delete(InspectionAction).where(InspectionAction.task_id == task_id))
                db.execute(delete(InspectionTask).where(InspectionTask.id == task_id))


def test_task_due_dates_and_demo_guard(accounts, monkeypatch):
    names, _, stations = accounts
    with SessionLocal.begin() as db:
        owner = db.scalar(select(User).where(User.username == names[0]))
        reg = SimpleNamespace(id=owner.id, role="regulator")
        for due in [now() - timedelta(days=1), now().replace(tzinfo=None)]:
            with pytest.raises(HTTPException) as e:
                inspection.create(db, reg, stations[0], "标题", "描述", "normal", due)
            assert e.value.status_code == 422
        monkeypatch.setattr(inspection.settings, "demo_mode", False)
        with pytest.raises(HTTPException) as e:
            inspection.create(
                db, reg, stations[0], "标题", "描述", "normal", now() + timedelta(days=1)
            )
        assert e.value.status_code == 409


def test_private_material_freeze_and_reply_binding(accounts, tmp_path, monkeypatch):
    import secrets
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app
    from app.models import InspectionAttachment, LoginSession

    names, password, stations = accounts
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    uid = None
    task_id = None
    try:
        with SessionLocal.begin() as db:
            owner = db.scalar(select(User).where(User.username == names[0]))
            reg = User(
                username="reg-test-" + secrets.token_hex(6),
                password_hash=owner.password_hash,
                role="regulator",
            )
            db.add(reg)
            db.flush()
            uid, regname = reg.id, reg.username
            task = inspection.create(
                db, reg, stations[0], "图文整改", "提交现场材料", "major", now() + timedelta(days=1)
            )
            task_id = task.id
        with (
            TestClient(app, client=("test-material-" + secrets.token_hex(6), 1)) as op,
            TestClient(app, client=("test-other-" + secrets.token_hex(6), 1)) as other,
            TestClient(app, client=("test-reg-" + secrets.token_hex(6), 1)) as regc,
        ):

            def login(client, username):
                r = client.post(
                    "/api/v1/auth/login", json={"username": username, "password": password}
                )
                assert r.status_code == 200
                return {"x-csrf-token": r.json()["csrf_token"]}

            headers = login(op, names[0])
            other_headers = login(other, names[1])
            reg_headers = login(regc, regname)
            url = f"/api/v1/inspections/{task_id}/attachments"
            bad = op.post(
                url,
                headers=headers,
                files={"file": ("x.html", b"<script>bad</script>", "image/png")},
            )
            assert bad.status_code == 415
            assert not list(Path(tmp_path).iterdir())
            uploaded = op.post(
                url,
                headers=headers,
                files={"file": ("现场照片.png", b"\x89PNG\r\n\x1a\nexample", "image/png")},
            )
            assert uploaded.status_code == 201
            aid = uploaded.json()["id"]
            download = f"{url}/{aid}/download"
            assert other.get(download).status_code == 404
            assert other.delete(f"{url}/{aid}", headers=other_headers).status_code == 404
            assert regc.get(download).status_code == 200
            assert (
                op.post(
                    f"/api/v1/inspections/{task_id}/actions",
                    headers=headers,
                    json={
                        "version": 1,
                        "action": "reply",
                        "comment": "已上传照片",
                        "contact": {"name": "Executor", "phone": "13800000001"},
                        "performed_at": (now() - timedelta(days=1)).isoformat(),
                    },
                ).status_code
                == 200
            )
            item = regc.get(url).json()["items"][0]
            assert item["action_id"] is not None
            assert op.delete(f"{url}/{aid}", headers=headers).status_code == 409
            assert (
                regc.post(
                    f"/api/v1/inspections/{task_id}/actions",
                    headers=reg_headers,
                    json={"version": 2, "action": "return", "comment": "补充说明"},
                ).status_code
                == 200
            )
            assert op.delete(f"{url}/{aid}", headers=headers).status_code == 409
            # New draft material may be removed, previously submitted evidence remains.
            second = op.post(
                url,
                headers=headers,
                files={"file": ("补充.pdf", b"%PDF-1.4\nexample", "application/pdf")},
            ).json()["id"]
            assert op.delete(f"{url}/{second}", headers=headers).status_code == 200
            assert len(regc.get(url).json()["items"]) == 1
    finally:
        with SessionLocal.begin() as db:
            if task_id:
                db.execute(
                    delete(InspectionAttachment).where(InspectionAttachment.task_id == task_id)
                )
                db.execute(delete(InspectionAction).where(InspectionAction.task_id == task_id))
                db.execute(delete(InspectionTask).where(InspectionTask.id == task_id))
            if uid:
                db.execute(delete(Audit).where(Audit.user_id == uid))
                db.execute(delete(LoginSession).where(LoginSession.user_id == uid))
                db.execute(delete(User).where(User.id == uid))
