"""Notice replies with private PNG/JPG images, committed together and immutable afterwards."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select

from app import content
from app.authorization import authorize_business_write
from app.config import settings
from app.dependencies import DB, Actor
from app.models import ArticleReply, ArticleReplyAttachment
from app.storage import store_upload

router = APIRouter(prefix="/api/v1/content", tags=["content"])


@router.post("/{article_id}/replies/with-images", status_code=201)
def reply_images(
    article_id: int,
    request: Request,
    user: Actor,
    db: DB,
    files: Annotated[list[UploadFile], File()],
    body: str = Form(min_length=1, max_length=4000),
    contact_name: str | None = Form(None, max_length=100),
    contact_phone: str | None = Form(None, max_length=80),
):
    authorize_business_write(db, user, request, "content.reply")
    content.demo_only()
    article = content.access(db, user, article_id, False, True)
    if user.role != "operator" or user.operator_id is None or article.kind != "notice":
        raise HTTPException(403, "仅所属企业可回复通知")
    if not body.strip() or not 1 <= len(files) <= 10:
        raise HTTPException(422, "请填写回复正文，每条图文回复需1至10张图片")
    contact_name, contact_phone = content.reply_contact(contact_name, contact_phone)
    paths = []
    try:
        reply = ArticleReply(
            article_id=article.id,
            operator_id=user.operator_id,
            actor_id=user.id,
            body=body.strip(),
            contact_name=contact_name,
            contact_phone=contact_phone,
        )
        db.add(reply)
        db.flush()
        for file in files:
            values, path = store_upload(file, allowed_types={"image/png", "image/jpeg"})
            paths.append(path)
            db.add(ArticleReplyAttachment(reply_id=reply.id, uploaded_by=user.id, **values))
        db.commit()
        return {"id": reply.id}
    except Exception:
        db.rollback()
        for path in paths:
            path.unlink(missing_ok=True)
        raise
    finally:
        for file in files:
            file.file.close()


@router.get("/{article_id}/replies/{reply_id}/images/{image_id}")
def image(article_id: int, reply_id: int, image_id: int, user: Actor, db: DB, manage: bool = False):
    content.access(db, user, article_id, manage)
    reply = db.scalar(
        content.replies_visible(select(ArticleReply).where(ArticleReply.id == reply_id), user)
    )
    if (
        not reply
        or reply.article_id != article_id
        or (user.role != "regulator" and reply.operator_id != user.operator_id)
    ):
        raise HTTPException(404, "回复不存在或不可访问")
    image = db.scalar(
        select(ArticleReplyAttachment).where(
            ArticleReplyAttachment.id == image_id, ArticleReplyAttachment.reply_id == reply_id
        )
    )
    if not image:
        raise HTTPException(404, "图片不存在")
    path = Path(settings.storage_path).resolve() / image.storage_key
    if not path.is_file():
        raise HTTPException(404, "图片文件不可用")
    return FileResponse(
        path,
        media_type=image.content_type,
        headers={
            "Content-Security-Policy": "sandbox",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )
