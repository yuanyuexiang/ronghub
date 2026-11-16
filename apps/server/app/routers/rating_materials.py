"""Categorized star-rating materials, including opaque archives; never unpack uploads."""

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, select

from app import rating
from app.authorization import authorize_business_write
from app.business_contracts import RatingMaterialItem, RatingMaterialList
from app.config import settings
from app.contracts import OkResponse
from app.dependencies import DB, Actor
from app.material_archive import archive as build_archive
from app.material_contracts import binary_responses
from app.models import Audit, RatingAttachment
from app.routers.attachments import metadata
from app.storage import store_upload

router = APIRouter(prefix="/api/v1/ratings/applications", tags=["ratings"])
LIMIT = 20 * 1024 * 1024
CATEGORIES = {
    "inspection_report": "第三方检测报告",
    "site_plan": "站点平面图",
    "service_record": "服务记录",
    "safety_record": "安全管理记录",
    "field_record": "现场记录",
    "other": "其他补充材料",
}


def writable(db, user, aid):
    rating.demo_only()
    a = rating.access(db, user, aid, True)
    if user.role != "operator" or a.status not in {"draft", "rejected"}:
        raise HTTPException(409, "仅所属运营商可在草稿或驳回状态补充材料")
    return a


def item(record):
    return {
        **metadata(record),
        "category": record.category,
        "category_name": CATEGORIES.get(record.category, "历史类别未识别"),
        "submitted_version": record.submitted_version,
    }


@router.get("/{application_id}/attachments", response_model=RatingMaterialList)
def listing(application_id: int, user: Actor, db: DB):
    rating.access(db, user, application_id)
    files = list(
        db.scalars(
            select(RatingAttachment)
            .where(RatingAttachment.application_id == application_id)
            .order_by(RatingAttachment.id)
        )
    )
    return {
        "items": [item(record) for record in files],
        "categories": CATEGORIES,
        "total_bytes": sum(a.size for a in files),
        "limit_bytes": LIMIT,
    }


@router.post("/{application_id}/attachments", status_code=201, response_model=RatingMaterialItem)
def upload(
    application_id: int,
    file: UploadFile,
    user: Actor,
    db: DB,
    request: Request,
    category: str = Form(...),
):
    authorize_business_write(db, user, request, "ratings.materials")
    writable(db, user, application_id)
    if category not in CATEGORIES:
        raise HTTPException(422, "材料类别不正确")
    total = db.scalar(
        select(func.coalesce(func.sum(RatingAttachment.size), 0)).where(
            RatingAttachment.application_id == application_id
        )
    )
    count = db.scalar(
        select(func.count())
        .select_from(RatingAttachment)
        .where(RatingAttachment.application_id == application_id)
    )
    if count >= 30:
        raise HTTPException(422, "每份星级申请最多30份材料")
    remaining = LIMIT - total
    if remaining <= 0:
        raise HTTPException(413, "全部材料总量不能超过20MB")
    values, path = store_upload(file, max_bytes=remaining, allow_archives=True)
    try:
        a = RatingAttachment(
            application_id=application_id, uploaded_by=user.id, category=category, **values
        )
        db.add(a)
        db.flush()
        result = RatingMaterialItem.model_validate(item(a))
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    return result


@router.get(
    "/{application_id}/attachments/archive",
    response_class=Response,
    responses={
        200: {"content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}}
    },
)
def archive(application_id: int, user: Actor, db: DB):
    # Same station→application lock order as upload/delete/submit; hold until bytes exist.
    application = rating.access(db, user, application_id, True)
    records = list(
        db.scalars(
            select(RatingAttachment)
            .where(RatingAttachment.application_id == application_id)
            .order_by(RatingAttachment.id)
        )
    )
    content = build_archive(Path(settings.storage_path).resolve(), application, records, LIMIT)
    db.add(
        Audit(
            user_id=user.id,
            action="ratings.materials.archive",
            object_id=application_id,
            detail={
                "files": len(records),
                "bytes": sum(record.size for record in records),
                "application_version": application.version,
            },
        )
    )
    db.commit()
    return Response(
        content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="ronghub-rating-{application_id}-materials.zip"',
            "Cache-Control": "no-store",
            "Content-Security-Policy": "sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/{application_id}/attachments/{material_id}/download",
    response_class=FileResponse,
    responses=binary_responses(
        "application/pdf", "image/png", "image/jpeg", "application/zip", "application/vnd.rar"
    ),
)
def download(application_id: int, material_id: int, user: Actor, db: DB):
    rating.access(db, user, application_id)
    a = db.get(RatingAttachment, material_id)
    if not a or a.application_id != application_id:
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


@router.delete("/{application_id}/attachments/{material_id}", response_model=OkResponse)
def remove(application_id: int, material_id: int, user: Actor, db: DB, request: Request):
    authorize_business_write(db, user, request, "ratings.materials")
    writable(db, user, application_id)
    a = db.get(RatingAttachment, material_id)
    if not a or a.application_id != application_id:
        raise HTTPException(404, "材料不存在")
    if a.submitted_version is not None:
        raise HTTPException(409, "已提交的历史证据不可删除，可新增补充材料")
    path = Path(settings.storage_path).resolve() / a.storage_key
    db.delete(a)
    db.commit()
    path.unlink(missing_ok=True)
    return {"ok": True}
