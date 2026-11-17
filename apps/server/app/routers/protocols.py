"""Authenticated reading of published customer protocol content; management reuses publication."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app import article_assets, content
from app.contracts import ContentAttachment
from app.dependencies import SnapshotActor
from app.models import Article, ArticleAttachment
from app.reporting import SnapshotDB
from app.richtext import RichDocument

router = APIRouter(prefix="/api/v1/protocols", tags=["protocols"])


class ProtocolSummary(BaseModel):
    id: int
    title: str
    published_at: datetime
    attachment_count: int
    is_demo: bool


class ProtocolPage(BaseModel):
    items: list[ProtocolSummary]
    total: int
    page: int
    page_size: int


class ProtocolDetail(ProtocolSummary):
    body: str
    rich_document: RichDocument | None
    attachments: list[ContentAttachment]


@router.get("", response_model=ProtocolPage)
def listing(
    user: SnapshotActor,
    db: SnapshotDB,
    q: str = Query("", max_length=150),
    page: int = Query(1, ge=1, le=2147483647),
):
    count = (
        select(func.count())
        .select_from(ArticleAttachment)
        .where(ArticleAttachment.article_id == Article.id)
        .correlate(Article)
        .scalar_subquery()
    )
    stmt = content.visible(select(Article, count.label("attachment_count")), user).where(
        Article.kind == "protocol"
    )
    if q.strip():
        stmt = stmt.where(Article.title.icontains(q.strip(), autoescape=True))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(
        stmt.order_by(Article.published_at.desc(), Article.id.desc())
        .offset((page - 1) * 20)
        .limit(20)
    )
    return {
        "items": [
            {
                "id": item.id,
                "title": item.title,
                "published_at": item.published_at,
                "attachment_count": files,
                "is_demo": item.is_demo,
            }
            for item, files in rows
        ],
        "total": total,
        "page": page,
        "page_size": 20,
    }


@router.get("/{article_id}", response_model=ProtocolDetail)
def detail(article_id: int, user: SnapshotActor, db: SnapshotDB):
    article = db.scalar(
        content.visible(select(Article), user).where(
            Article.id == article_id, Article.kind == "protocol"
        )
    )
    if article is None:
        raise HTTPException(404, "协议未发布或不可访问")
    assets = [article_assets.output(item) for item in article_assets.records(db, article.id)]
    return {
        "id": article.id,
        "title": article.title,
        "published_at": article.published_at,
        "body": article.body,
        "rich_document": article.rich_document,
        "is_demo": article.is_demo,
        "attachment_count": len(assets),
        "attachments": assets,
    }
