from datetime import timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from test_business_exports import login

from app import inspection, workflows
from app.config import settings
from app.db import SessionLocal
from app.models import Attachment, InspectionAction, InspectionAttachment, InspectionTask, User, now
from app.routers import attachments, inspection_materials


@pytest.mark.parametrize("kind", ["application", "inspection"])
def test_upload_response_failure_cleans_record_and_file_before_retry(
    accounts, tmp_path, monkeypatch, kind
):
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    names, password, stations = accounts
    with SessionLocal.begin() as db:
        owner = db.scalar(select(User).where(User.username == names[0]))
        if kind == "application":
            record = workflows.create(db, owner, stations[0], "access", {})
        else:
            record = inspection.create(
                db,
                SimpleNamespace(id=owner.id, role="regulator"),
                stations[0],
                "材料投影",
                "现场材料",
                "normal",
                now() + timedelta(days=1),
            )
        record_id = record.id
    client = login(names[0], password)
    headers = {"x-csrf-token": client.get("/api/v1/auth/me").json()["csrf_token"]}
    url = f"/api/v1/{'applications' if kind == 'application' else 'inspections'}/{record_id}/attachments"
    module = attachments if kind == "application" else inspection_materials
    model = Attachment if kind == "application" else InspectionAttachment
    condition = (
        model.application_id == record_id if kind == "application" else model.task_id == record_id
    )
    original = module.metadata
    try:
        monkeypatch.setattr(module, "metadata", lambda row: {**original(row), "size": -1})
        with pytest.raises(ValidationError):
            client.post(url, headers=headers, files={"file": ("材料.pdf", b"%PDF-1.4 evidence")})
        assert not list(tmp_path.iterdir())
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(model).where(condition)) == 0
        monkeypatch.setattr(module, "metadata", original)
        response = client.post(
            url, headers=headers, files={"file": ("材料.pdf", b"%PDF-1.4 evidence")}
        )
        assert response.status_code == 201, response.text
        item = response.json()
        assert item["content_type"] == "application/pdf"
        assert len(item["sha256"]) == 64 and item["created_at"]
        assert "storage_key" not in item
        assert client.get(url).json()["items"] == [item]
        if kind == "inspection":
            assert item["action_id"] is None
        download = (
            f"/api/v1/attachments/{item['id']}" if kind == "application" else f"{url}/{item['id']}"
        )
        result = client.get(download + "/download")
        assert result.headers["content-type"] == "application/pdf"
        assert result.content == b"%PDF-1.4 evidence"
        assert client.delete(download, headers=headers).json() == {"ok": True}
        assert client.get(url).json() == {"items": []}
        assert not list(tmp_path.iterdir())
    finally:
        client.close()
        with SessionLocal.begin() as db:
            db.execute(delete(model).where(condition))
            if kind == "inspection":
                db.execute(delete(InspectionAction).where(InspectionAction.task_id == record_id))
                db.execute(delete(InspectionTask).where(InspectionTask.id == record_id))
