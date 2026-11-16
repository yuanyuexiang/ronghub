"""Task-scoped evidence, frozen and bound to a reply when it is submitted."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from app import inspection
from app.authorization import authorize_business_write
from app.config import settings
from app.contracts import OkResponse
from app.dependencies import DB, Actor
from app.material_contracts import InspectionMaterialItem, InspectionMaterialList, binary_responses
from app.models import InspectionAttachment
from app.routers.attachments import metadata
from app.storage import store_upload

router = APIRouter(prefix="/api/v1/inspections", tags=["inspections"])


def writable(db, user, task_id):
    inspection.demo_only()
    task = inspection.access(db, user, task_id, True)
    if user.role != "operator" or task.status not in {"assigned", "returned"}:
        raise HTTPException(409, "仅所属企业可在整改中维护未提交材料")
    return task


@router.get("/{task_id}/attachments", response_model=InspectionMaterialList)
def listing(task_id: int, user: Actor, db: DB):
    inspection.access(db, user, task_id)
    return {
        "items": [
            {**metadata(a), "action_id": a.action_id}
            for a in db.scalars(
                select(InspectionAttachment)
                .where(InspectionAttachment.task_id == task_id)
                .order_by(InspectionAttachment.id)
            )
        ]
    }


@router.post("/{task_id}/attachments", status_code=201, response_model=InspectionMaterialItem)
def upload(task_id: int, file: UploadFile, request: Request, user: Actor, db: DB):
    authorize_business_write(db, user, request, "inspections.materials")
    writable(db, user, task_id)
    if (
        db.scalar(
            select(func.count())
            .select_from(InspectionAttachment)
            .where(InspectionAttachment.task_id == task_id)
        )
        >= 30
    ):
        raise HTTPException(422, "每项任务最多30份材料")
    values, path = store_upload(file)
    try:
        attachment = InspectionAttachment(task_id=task_id, uploaded_by=user.id, **values)
        db.add(attachment)
        db.flush()
        result = InspectionMaterialItem.model_validate(
            {**metadata(attachment), "action_id": attachment.action_id}
        )
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    return result


@router.get(
    "/{task_id}/attachments/{attachment_id}/download",
    response_class=FileResponse,
    responses=binary_responses("application/pdf", "image/png", "image/jpeg"),
)
def download(task_id: int, attachment_id: int, user: Actor, db: DB):
    inspection.access(db, user, task_id)
    a = db.get(InspectionAttachment, attachment_id)
    if not a or a.task_id != task_id:
        raise HTTPException(404, "材料不存在")
    path = Path(settings.storage_path).resolve() / a.storage_key
    if not path.is_file():
        raise HTTPException(404, "材料文件不可用")
    return FileResponse(
        path,
        filename=a.filename,
        media_type=a.content_type,
        headers={"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"},
    )


@router.delete("/{task_id}/attachments/{attachment_id}", response_model=OkResponse)
def remove(task_id: int, attachment_id: int, request: Request, user: Actor, db: DB):
    authorize_business_write(db, user, request, "inspections.materials")
    writable(db, user, task_id)
    a = db.get(InspectionAttachment, attachment_id)
    if not a or a.task_id != task_id:
        raise HTTPException(404, "材料不存在")
    if a.action_id is not None:
        raise HTTPException(409, "已提交的历史证据不可删除")
    path = Path(settings.storage_path).resolve() / a.storage_key
    db.delete(a)
    db.commit()
    path.unlink(missing_ok=True)
    return {"ok": True}
