"""Anonymous projections require explicit publication consent; authenticated-all is not public."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from app import article_assets
from app.config import settings
from app.models import Article, ArticleAttachment, now
from app.reporting import SnapshotDB
from app.richtext import RichDocument

router = APIRouter(prefix="/api/v1/public", tags=["public portal"])
PUBLIC_KINDS = ("announcement", "news", "contact")


class PortalInfo(BaseModel):
    site_name: str
    demo_mode: bool


class PublicArticleSummary(BaseModel):
    id: int
    kind: Literal["announcement", "news", "contact"]
    title: str
    excerpt: str
    published_at: datetime
    is_demo: bool


class PublicArticlePage(BaseModel):
    items: list[PublicArticleSummary]
    total: int
    page: int
    page_size: int


class PublicAttachment(BaseModel):
    id: int
    filename: str
    content_type: str
    size: int
    sha256: str


class PublicArticleDetail(PublicArticleSummary):
    body: str
    rich_document: RichDocument | None
    attachments: list[PublicAttachment]


def visible(stmt, at):
    return stmt.where(
        Article.portal_public.is_(True),
        Article.status == "published",
        Article.published_at <= at,
        Article.kind.in_(PUBLIC_KINDS),
        Article.audience == "all",
        Article.operator_id.is_(None),
        Article.recipient_ids.is_(None),
    )


def public_article(db, article_id):
    row = db.scalar(visible(select(Article), now()).where(Article.id == article_id))
    if row is None:
        raise HTTPException(404, "资讯未公开或已撤下")
    return row


def summary(row):
    return {
        "id": row.id,
        "kind": row.kind,
        "title": row.title,
        "excerpt": row.body[:180],
        "published_at": row.published_at,
        "is_demo": row.is_demo,
    }


@router.get("/info", response_model=PortalInfo)
def info():
    return {"site_name": "福州市充电监管平台", "demo_mode": settings.demo_mode}


@router.get("/articles", response_model=PublicArticlePage)
def listing(
    db: SnapshotDB,
    q: str = Query("", max_length=150),
    kind: Literal["", "announcement", "news", "contact"] = "",
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=50),
):
    if page_size not in {10, 20, 50}:
        raise HTTPException(422, "每页条数须为10、20或50")
    stmt = visible(select(Article), now()).where(
        Article.kind == kind if kind else Article.kind.in_(["announcement", "news"])
    )
    if q.strip():
        stmt = stmt.where(Article.title.icontains(q.strip(), autoescape=True))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(
        stmt.order_by(Article.published_at.desc(), Article.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [summary(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/articles/{article_id}", response_model=PublicArticleDetail)
def detail(article_id: int, db: SnapshotDB):
    row = public_article(db, article_id)
    assets = db.scalars(
        select(ArticleAttachment)
        .where(ArticleAttachment.article_id == row.id, ArticleAttachment.frozen.is_(True))
        .order_by(ArticleAttachment.id)
    )
    return {
        **summary(row),
        "body": row.body,
        "rich_document": row.rich_document,
        "attachments": [
            {
                "id": item.id,
                "filename": item.filename,
                "content_type": item.content_type,
                "size": item.size,
                "sha256": item.sha256,
            }
            for item in assets
        ],
    }


@router.get(
    "/articles/{article_id}/attachments/{attachment_id}",
    response_class=FileResponse,
    responses={
        200: {
            "content": {
                mime: {"schema": {"type": "string", "format": "binary"}}
                for mime in [
                    "application/pdf",
                    "image/png",
                    "image/jpeg",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ]
            }
        }
    },
)
def download(article_id: int, attachment_id: int, db: SnapshotDB, inline: bool = False):
    public_article(db, article_id)
    asset = db.scalar(
        select(ArticleAttachment).where(
            ArticleAttachment.id == attachment_id,
            ArticleAttachment.article_id == article_id,
            ArticleAttachment.frozen.is_(True),
        )
    )
    if asset is None:
        raise HTTPException(404, "公开附件不存在或不可访问")
    path = article_assets.verified_path(asset)
    headers = {
        "Cache-Control": "no-store",
        "Content-Security-Policy": "sandbox",
        "X-Content-Type-Options": "nosniff",
    }
    if inline and asset.content_type in {"image/png", "image/jpeg"}:
        headers["Content-Disposition"] = "inline"
        return FileResponse(path, media_type=asset.content_type, headers=headers)
    return FileResponse(
        path, media_type=asset.content_type, filename=asset.filename, headers=headers
    )
