"""Private local attachment storage; no public mount and no user-controlled paths."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from app.authorization import authorize_business_write
from app.config import settings
from app.contracts import OkResponse
from app.dependencies import DB, Actor
from app.material_contracts import (
    ApplicationMaterialItem,
    ApplicationMaterialList,
    binary_responses,
)
from app.models import Attachment, StationApplication
from app.scopes import records as scoped_records
from app.storage import store_upload
from app.workflows import allowed_demo_rules

router = APIRouter()


def application_access(db, user, aid, write=False):
    stmt = scoped_records(select(StationApplication), StationApplication, user).where(
        StationApplication.id == aid
    )
    a = db.scalar(stmt.with_for_update() if write else stmt)
    if not a or (user.role == "operator" and a.operator_id != user.operator_id):
        raise HTTPException(404, "申请不存在或不可访问")
    if user.role not in {"operator", "regulator"}:
        raise HTTPException(403, "无权访问申请材料")
    if write and (user.role != "operator" or a.status not in {"draft", "rejected"}):
        raise HTTPException(409, "仅所属运营商可在草稿或驳回状态维护材料")
    return a


def metadata(a):
    return {
        "id": a.id,
        "filename": a.filename,
        "size": a.size,
        "content_type": a.content_type,
        "sha256": a.sha256,
        "created_at": a.created_at.isoformat(),
    }


@router.get(
    "/api/v1/applications/{application_id}/attachments", response_model=ApplicationMaterialList
)
def list_attachments(application_id: int, user: Actor, db: DB):
    application_access(db, user, application_id)
    return {
        "items": [
            metadata(a)
            for a in db.scalars(
                select(Attachment)
                .where(Attachment.application_id == application_id)
                .order_by(Attachment.id)
            )
        ]
    }


@router.post(
    "/api/v1/applications/{application_id}/attachments",
    status_code=201,
    response_model=ApplicationMaterialItem,
)
def upload(application_id: int, file: UploadFile, user: Actor, db: DB, request: Request):
    authorize_business_write(db, user, request, "applications.materials")
    allowed_demo_rules()
    application_access(db, user, application_id, write=True)
    if (
        db.scalar(
            select(func.count())
            .select_from(Attachment)
            .where(Attachment.application_id == application_id)
        )
        >= 10
    ):
        raise HTTPException(422, "每份申请最多10份材料")
    values, path = store_upload(file)
    try:
        a = Attachment(application_id=application_id, uploaded_by=user.id, **values)
        db.add(a)
        db.flush()
        result = ApplicationMaterialItem.model_validate(metadata(a))
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
def download(attachment_id: int, user: Actor, db: DB):
    a = db.get(Attachment, attachment_id)
    if not a:
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
    if not a:
        raise HTTPException(404, "材料不存在")
    application_access(db, user, a.application_id, write=True)
    path = Path(settings.storage_path).resolve() / a.storage_key
    db.delete(a)
    db.commit()
    path.unlink(missing_ok=True)
    return {"ok": True}
