"""Private article attachments and inline images, with draft versioning and frozen manifests."""

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select

from app import article_assets, content
from app.authorization import authorize_business_write, require
from app.contracts import ContentAssetMutation
from app.dependencies import DB, Actor
from app.models import ArticleAttachment
from app.richtext import image_ids
from app.storage import OFFICE_TYPES, store_upload

router = APIRouter(prefix="/api/v1/content", tags=["content"])
ALLOWED = {"application/pdf", "image/png", "image/jpeg"} | OFFICE_TYPES


def draft(db, user, request, article_id, version):
    authorize_business_write(db, user, request, "content.edit")
    require(db, user, "content.manage")
    content.demo_only()
    article = content.access(db, user, article_id, True, True)
    if article.status != "draft" or article.version != version:
        raise HTTPException(409, "内容已更新或已发布，请刷新草稿")
    return article


def response(db, article):
    return {
        "article": content.output(article),
        "attachments": [
            article_assets.output(row) for row in article_assets.records(db, article.id)
        ],
    }


@router.post("/{article_id}/attachments", response_model=ContentAssetMutation, status_code=201)
def upload(
    article_id: int,
    request: Request,
    user: Actor,
    db: DB,
    file: Annotated[UploadFile, File()],
    version: Annotated[int, Form(ge=1)],
):
    article = draft(db, user, request, article_id, version)
    existing = article_assets.records(db, article.id)
    if len(existing) >= 10:
        raise HTTPException(422, "每篇内容最多10份附件")
    remaining = 50 * 1024 * 1024 - sum(row.size for row in existing)
    path = None
    try:
        values, path = store_upload(
            file,
            max_bytes=min(10 * 1024 * 1024, remaining),
            allowed_types=ALLOWED,
            allow_office=True,
        )
        db.add(ArticleAttachment(article_id=article.id, uploaded_by=user.id, **values))
        article.version += 1
        content.record(db, user, article, "attachment_upload")
        result = response(db, article)
        db.commit()
        return result
    except Exception:
        db.rollback()
        if path:
            path.unlink(missing_ok=True)
        raise
    finally:
        file.file.close()


@router.delete("/{article_id}/attachments/{attachment_id}", response_model=ContentAssetMutation)
def delete(
    article_id: int,
    attachment_id: int,
    request: Request,
    user: Actor,
    db: DB,
    version: int = Query(..., ge=1),
):
    article = draft(db, user, request, article_id, version)
    asset = db.scalar(
        select(ArticleAttachment).where(
            ArticleAttachment.id == attachment_id, ArticleAttachment.article_id == article.id
        )
    )
    if not asset:
        raise HTTPException(404, "附件不存在")
    if asset.frozen or attachment_id in (
        image_ids(article.rich_document) if article.rich_document else set()
    ):
        raise HTTPException(409, "附件已冻结或仍被已保存正文引用，请先移除正文图片并保存")
    path = article_assets.verified_path(asset)
    db.delete(asset)
    article.version += 1
    content.record(db, user, article, "attachment_delete")
    db.commit()
    path.unlink(missing_ok=True)
    return response(db, article)


@router.get(
    "/{article_id}/attachments/{attachment_id}",
    response_class=FileResponse,
    responses={
        200: {
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            }
        }
    },
)
def download(
    article_id: int,
    attachment_id: int,
    user: Actor,
    db: DB,
    manage: bool = False,
    inline: bool = False,
):
    content.access(db, user, article_id, manage)
    asset = db.scalar(
        select(ArticleAttachment).where(
            ArticleAttachment.id == attachment_id, ArticleAttachment.article_id == article_id
        )
    )
    if not asset:
        raise HTTPException(404, "附件不存在或不可访问")
    path = article_assets.verified_path(asset)
    if inline and asset.content_type in {"image/png", "image/jpeg"}:
        return FileResponse(
            path,
            media_type=asset.content_type,
            headers={
                "Content-Disposition": "inline",
                "Content-Security-Policy": "sandbox",
                "X-Content-Type-Options": "nosniff",
            },
        )
    return FileResponse(
        path,
        filename=asset.filename,
        media_type=asset.content_type,
        headers={"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"},
    )
