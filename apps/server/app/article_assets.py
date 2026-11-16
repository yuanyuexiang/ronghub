"""Private draft assets; body references and frozen manifests share article transaction locks."""

import hashlib
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select

from app.config import settings
from app.models import ArticleAttachment
from app.richtext import image_ids
from app.routers.attachments import metadata


def records(db, article_id):
    return list(
        db.scalars(
            select(ArticleAttachment)
            .where(ArticleAttachment.article_id == article_id)
            .order_by(ArticleAttachment.id)
        )
    )


def output(row):
    return {**metadata(row), "frozen": row.frozen}


def verify_references(db, article_id, document):
    ids = image_ids(document) if document else set()
    assets = {row.id: row for row in records(db, article_id)}
    if any(
        asset_id not in assets or assets[asset_id].content_type not in {"image/png", "image/jpeg"}
        for asset_id in ids
    ):
        raise HTTPException(422, "正文图片须引用本内容已上传的PNG/JPG附件")


def verified_path(row):
    path = Path(settings.storage_path).resolve() / row.storage_key
    if not path.is_file() or path.is_symlink() or path.stat().st_size != row.size:
        raise HTTPException(409, "内容附件文件缺失或不完整，请核查后再发布/下载")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(65536):
            digest.update(chunk)
    if digest.hexdigest() != row.sha256:
        raise HTTPException(409, "内容附件摘要不一致，请核查文件完整性")
    return path
