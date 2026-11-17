"""Private local attachment storage; no public mount and no user-controlled paths."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from app.authorization import authorize_business_write
from app.config import settings
from app.contracts import OkResponse
from app.dependencies import DB, Actor, SnapshotActor
from app.material_contracts import (
    ApplicationAttachmentItem,
    ApplicationAttachmentList,
    binary_responses,
)
from app.models import Attachment, StationApplication
from app.reporting import SnapshotDB
from app.scopes import records as scoped_records
from app.storage import store_upload
from app.workflows import allowed_demo_rules

router = APIRouter()


def application_access(db, user, aid, write=False):
    stmt = scoped_records(select(StationApplication), StationApplication, user).where(
        StationApplication.id == aid
    )
    a = db.scalar(
        stmt.with_for_update().execution_options(populate_existing=True) if write else stmt
    )
    if not a or (user.role == "operator" and a.operator_id != user.operator_id):
        raise HTTPException(404, "申请不存在或不可访问")
    if user.role not in {"operator", "regulator"}:
        raise HTTPException(403, "无权访问申请材料")
    if write and (user.role != "operator" or a.status not in {"draft", "rejected"}):
        raise HTTPException(409, "仅所属运营商可在草稿或驳回状态维护材料")
    return a


def metadata(a):
    result = {
        "id": a.id,
        "filename": a.filename,
        "size": a.size,
        "content_type": a.content_type,
        "sha256": a.sha256,
        "created_at": a.created_at.isoformat(),
    }
    if isinstance(a, Attachment):
        result["category"] = a.category
        result["revision"] = a.revision
    return result


@router.get(
    "/api/v1/applications/{application_id}/attachments", response_model=ApplicationAttachmentList
)
def list_attachments(application_id: int, user: SnapshotActor, db: SnapshotDB):
    application_access(db, user, application_id)
    return {
        "items": [
            metadata(a)
            for a in db.scalars(
                select(Attachment)
                .where(Attachment.application_id == application_id, Attachment.active.is_(True))
                .order_by(Attachment.id)
            )
        ]
    }


@router.post(
    "/api/v1/applications/{application_id}/attachments",
    status_code=201,
    response_model=ApplicationAttachmentItem,
)
def upload(application_id: int, file: UploadFile, user: Actor, db: DB, request: Request):
    authorize_business_write(db, user, request, "applications.materials")
    allowed_demo_rules()
    application_access(db, user, application_id, write=True)
    if (
        db.scalar(
            select(func.count())
            .select_from(Attachment)
            .where(Attachment.application_id == application_id, Attachment.active.is_(True))
        )
        >= 10
    ):
        raise HTTPException(422, "每份申请最多10份材料")
    values, path = store_upload(file)
    try:
        a = Attachment(application_id=application_id, uploaded_by=user.id, **values)
        db.add(a)
        db.flush()
        result = ApplicationAttachmentItem.model_validate(metadata(a))
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    return result


@router.get(
    "/api/v1/attachments/{attachment_id}/download",
    response_class=FileResponse,
    responses=binary_responses("application/pdf", "image/png", "image/jpeg"),
)
def download(attachment_id: int, user: SnapshotActor, db: SnapshotDB):
    a = db.get(Attachment, attachment_id)
    if not a or not a.active:
        raise HTTPException(404, "材料不存在")
    application_access(db, user, a.application_id)
    path = Path(settings.storage_path).resolve() / a.storage_key
    if not path.is_file():
        raise HTTPException(404, "材料文件不可用，请联系管理员")
    return FileResponse(
        path,
        filename=a.filename,
        media_type=a.content_type,
        headers={"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"},
    )


@router.delete("/api/v1/attachments/{attachment_id}", response_model=OkResponse)
def remove(attachment_id: int, user: Actor, db: DB, request: Request):
    authorize_business_write(db, user, request, "applications.materials")
    allowed_demo_rules()
    a = db.get(Attachment, attachment_id)
    if not a or not a.active:
        raise HTTPException(404, "材料不存在")
    application_access(db, user, a.application_id, write=True)
    path = Path(settings.storage_path).resolve() / a.storage_key
    result = OkResponse.model_validate({"ok": True})
    from app.application_evidence import material_was_submitted

    retained = material_was_submitted(db, a)
    if retained:
        a.active = False
    else:
        db.delete(a)
    db.commit()
    if not retained:
        path.unlink(missing_ok=True)
    return result
