import csv
import io
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select

from app import article_assets, content, reply_documents
from app.authorization import CONTENT_ACTIONS, authorize_business_write, require
from app.contracts import (
    ContentArticle,
    ContentBatchResult,
    ContentDetail,
    ContentOperatorPage,
    ContentPage,
    ContentReplyCreated,
    NoticeRecordPage,
)
from app.dependencies import DB, Actor, SnapshotActor
from app.models import (
    Article,
    ArticleAction,
    ArticleReply,
    ArticleReplyAttachment,
    Audit,
    NoticeDelivery,
    Operator,
    User,
)
from app.reporting import SnapshotDB
from app.richtext import RichDocument, plain_text
from app.routers.attachments import metadata

router = APIRouter(prefix="/api/v1/content", tags=["content"])


class ArticleInput(BaseModel):
    portal_public: bool = False
    kind: str
    title: str = Field(min_length=1, max_length=150)
    body: str = Field("", max_length=20000)
    rich_document: RichDocument | None = None
    audience: str = "all"
    operator_id: int | None = None
    recipient_ids: list[Annotated[int, Field(strict=True, gt=0)]] | None = Field(
        None, min_length=1, max_length=100
    )

    @model_validator(mode="after")
    def validate_article(self):
        if self.kind not in {
            "announcement",
            "news",
            "contact",
            "notice",
            "protocol",
        } or self.audience not in {
            "all",
            "regulator",
            "association",
            "operator",
        }:
            raise ValueError("不支持的内容类型或受众")
        if self.portal_public and (
            self.kind not in {"announcement", "news", "contact"}
            or self.audience != "all"
            or self.operator_id is not None
            or self.recipient_ids is not None
        ):
            raise ValueError("门户公开仅支持面向全体、无定向目标的公告/资讯/联系方式")
        if self.operator_id is not None and self.audience != "operator":
            raise ValueError("定向企业内容的受众必须为运营商")
        if self.recipient_ids is not None:
            if self.kind != "notice" or self.audience != "operator" or self.operator_id is not None:
                raise ValueError("多企业通知需运营商受众，且不能同时指定旧单企业字段")
            if any(type(i) is not int or i <= 0 for i in self.recipient_ids) or len(
                set(self.recipient_ids)
            ) != len(self.recipient_ids):
                raise ValueError("请选择不重复的有效企业编号")
            self.recipient_ids = sorted(self.recipient_ids)
        if self.rich_document is not None:
            self.body = plain_text(self.rich_document.model_dump(exclude_none=True))
        if not self.title.strip() or not self.body.strip():
            raise ValueError("标题和正文不能为空")
        return self


class EditInput(ArticleInput):
    version: int = Field(ge=1)


class ActionInput(BaseModel):
    action: str
    version: int = Field(ge=1)
    published_at: datetime | None = None


class BatchItem(BaseModel):
    id: Annotated[int, Field(strict=True, gt=0)]
    version: Annotated[int, Field(strict=True, ge=1)]


class BatchInput(BaseModel):
    action: str = Field(pattern="^(publish|delete)$")
    items: list[BatchItem] = Field(min_length=1, max_length=100)
    published_at: datetime | None = None

    @model_validator(mode="after")
    def validate_batch(self):
        if len({row.id for row in self.items}) != len(self.items):
            raise ValueError("批量内容编号不能重复")
        if self.published_at is not None and (
            self.published_at.tzinfo is None or self.action != "publish"
        ):
            raise ValueError("仅发布可指定包含时区的生效时间")
        return self


class ReplyInput(BaseModel):
    body: str = Field("", max_length=4000)
    rich_document: RichDocument | None = None
    contact_name: str | None = Field(None, max_length=100)
    contact_phone: str | None = Field(None, max_length=80)

    @model_validator(mode="after")
    def validate_contact(self):
        self.contact_name, self.contact_phone = content.reply_contact(
            self.contact_name, self.contact_phone
        )
        return self


@router.get("", response_model=ContentPage)
def listing(
    user: Actor,
    db: DB,
    manage: bool = False,
    page: int = Query(1, ge=1, le=2147483647),
    q: str = Query("", max_length=150),
    include_deleted: bool = False,
):
    stmt = content.visible(select(Article), user, manage)
    if not include_deleted:
        stmt = stmt.where(Article.status != "deleted")
    if q:
        stmt = stmt.where(Article.title.icontains(q.strip(), autoescape=True))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    return {
        "items": [
            content.output(a)
            for a in db.scalars(stmt.order_by(Article.id.desc()).offset((page - 1) * 20).limit(20))
        ],
        "total": total,
        "page": page,
        "page_size": 20,
    }


@router.post("", status_code=201, response_model=ContentArticle)
def create(body: ArticleInput, request: Request, user: Actor, db: DB):
    authorize_business_write(db, user, request, "content.create")
    require(db, user, "content.manage")
    content.demo_only()
    if body.portal_public:
        require(db, user, "content.public")
    if user.role != "regulator":
        raise HTTPException(403, "仅监管人员可管理内容")
    if body.operator_id is not None and not db.get(Operator, body.operator_id):
        raise HTTPException(422, "运营商不存在")
    if body.recipient_ids is not None:
        content.recipients(db, body.recipient_ids)
    document = body.rich_document.model_dump(exclude_none=True) if body.rich_document else None
    article_assets.verify_references(db, -1, document)
    article = Article(
        **body.model_dump(exclude={"rich_document"}), rich_document=document, created_by=user.id
    )
    db.add(article)
    content.record(db, user, article, "create")
    result = ContentArticle.model_validate(content.output(article))
    db.commit()
    return result


@router.post("/batch-actions", response_model=ContentBatchResult)
def batch_actions(body: BatchInput, request: Request, user: Actor, db: DB):
    authorize_business_write(db, user, request, CONTENT_ACTIONS[body.action])
    require(db, user, "content.manage")
    content.demo_only()
    versions = {row.id: row.version for row in body.items}
    rows = list(
        db.scalars(
            content.visible(select(Article).where(Article.id.in_(versions)), user, True)
            .order_by(Article.id)
            .with_for_update()
        )
    )
    if len(rows) != len(versions):
        raise HTTPException(404, "部分内容不存在或不可访问")
    for row in rows:
        if row.version != versions[row.id] or row.status != "draft":
            raise HTTPException(409, f"内容 #{row.id} 版本或状态已更新，整批未提交")
    if body.action == "publish":
        recipients = sorted({oid for row in rows for oid in (row.recipient_ids or [])})
        content.recipients(db, recipients)
    for row in rows:
        content.apply_action(db, user, row, body.action, versions[row.id], body.published_at)
    result = {"items": [content.output(row) for row in rows], "total": len(rows)}
    db.commit()
    return result


@router.get("/notice-records", response_model=NoticeRecordPage)
def notice_records(
    user: SnapshotActor,
    db: SnapshotDB,
    manage: bool = False,
    q: str = Query("", max_length=150),
    state: str = Query("", pattern="^(|scheduled|visible|withdrawn)$"),
    replied: bool | None = None,
    article_id: int | None = Query(None, gt=0),
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    stmt, stamp = notice_statement(db, user, manage, q, state, replied, article_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(
        stmt.order_by(NoticeDelivery.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return {
        "items": [
            {
                **delivery_output(row, article, count, latest, stamp),
                "article_id": article.id,
                "title": article.title,
                "is_demo": article.is_demo,
            }
            for row, article, count, latest in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def notice_statement(db, user, manage, q, state, replied, article_id):
    from app.models import now
    from app.scopes import require_global

    if manage:
        require(db, user, "content.manage")
        require_global(user)
    elif user.role != "operator":
        raise HTTPException(403, "仅企业可查看自身记录；监管请使用管理视图")
    counts = (
        select(
            ArticleReply.article_id.label("aid"),
            ArticleReply.operator_id.label("oid"),
            func.count().label("count"),
            func.max(ArticleReply.created_at).label("latest"),
        )
        .group_by(ArticleReply.article_id, ArticleReply.operator_id)
        .subquery()
    )
    stmt = content.visible(
        select(NoticeDelivery, Article, counts.c.count, counts.c.latest)
        .join(Article, Article.id == NoticeDelivery.article_id)
        .outerjoin(
            counts,
            (counts.c.aid == NoticeDelivery.article_id)
            & (counts.c.oid == NoticeDelivery.operator_id),
        ),
        user,
        manage,
    )
    if not manage:
        stmt = stmt.where(NoticeDelivery.operator_id == user.operator_id)
    if article_id is not None:
        stmt = stmt.where(Article.id == article_id)
    if q.strip():
        stmt = stmt.where(
            Article.title.icontains(q.strip(), autoescape=True)
            | NoticeDelivery.operator_name.icontains(q.strip(), autoescape=True)
        )
    stamp = now()
    if state == "withdrawn":
        stmt = stmt.where(Article.status == "withdrawn")
    elif state == "scheduled":
        stmt = stmt.where(Article.status == "published", Article.published_at > stamp)
    elif state == "visible":
        stmt = stmt.where(Article.status == "published", Article.published_at <= stamp)
    if replied is not None:
        stmt = stmt.where(counts.c.count > 0 if replied else counts.c.count.is_(None))
    return stmt, stamp


@router.get(
    "/export",
    response_class=Response,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string"}}}}},
)
def export_articles(
    user: SnapshotActor,
    db: SnapshotDB,
    q: str = Query("", max_length=150),
    include_deleted: bool = False,
):
    export_guard(db, user)
    stmt = content.visible(
        select(
            Article.id,
            Article.title,
            Article.kind,
            Article.status,
            Article.version,
            Article.published_at,
            Article.is_demo,
        ),
        user,
        True,
    )
    if not include_deleted:
        stmt = stmt.where(Article.status != "deleted")
    if q.strip():
        stmt = stmt.where(Article.title.icontains(q.strip(), autoescape=True))
    rows = list(db.execute(stmt.order_by(Article.id.desc()).limit(10001)))
    return metadata_csv(
        db,
        user,
        "content.export",
        "ronghub-content.csv",
        ["内容编号", "标题", "类型", "状态", "版本", "计划生效时间(带时区)", "演示标识"],
        rows,
    )


@router.get(
    "/notice-records/export",
    response_class=Response,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string"}}}}},
)
def export_notice_records(
    user: SnapshotActor,
    db: SnapshotDB,
    q: str = Query("", max_length=150),
    state: str = Query("", pattern="^(|scheduled|visible|withdrawn)$"),
    replied: bool | None = None,
    article_id: int | None = Query(None, gt=0),
):
    from sqlalchemy.orm import load_only

    export_guard(db, user)
    stmt, stamp = notice_statement(db, user, True, q, state, replied, article_id)
    rows = list(
        db.execute(
            stmt.options(
                load_only(
                    Article.id, Article.title, Article.status, Article.published_at, Article.is_demo
                ),
                load_only(
                    NoticeDelivery.id,
                    NoticeDelivery.article_id,
                    NoticeDelivery.operator_id,
                    NoticeDelivery.operator_name,
                    NoticeDelivery.publication_version,
                    NoticeDelivery.scheduled_at,
                ),
            )
            .order_by(NoticeDelivery.id.desc())
            .limit(10001)
        )
    )
    values = [
        (
            row.id,
            article.id,
            article.title,
            row.operator_id,
            row.operator_name,
            row.publication_version,
            row.scheduled_at.isoformat(),
            "withdrawn"
            if article.status == "withdrawn"
            else "scheduled"
            if article.published_at > stamp
            else "visible",
            count or 0,
            latest.isoformat() if latest else None,
            article.is_demo,
        )
        for row, article, count, latest in rows
    ]
    return metadata_csv(
        db,
        user,
        "content.notice-records.export",
        "ronghub-notice-records.csv",
        [
            "记录编号",
            "内容编号",
            "通知标题",
            "企业编号",
            "企业名称(发布快照)",
            "发布版本",
            "计划生效时间(带时区)",
            "站内状态(非外部送达)",
            "回复条数",
            "最后回复时间(带时区)",
            "演示标识",
        ],
        values,
    )


def export_guard(db, user):
    from app.scopes import require_global

    require(db, user, "content.read")

    require(db, user, "content.manage")
    require(db, user, "content.export")
    require_global(user)
    if user.role != "regulator":
        raise HTTPException(403, "仅全域监管可导出内容元数据")


def metadata_csv(db, user, action, filename, headers, rows):
    from app.routers.statistics import csv_cell

    if len(rows) > 10000:
        raise HTTPException(422, "单次最多导出10000条，请缩小范围")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(
            [csv_cell(value.isoformat() if isinstance(value, datetime) else value) for value in row]
        )
    db.add(Audit(user_id=user.id, action=action, detail={"rows": len(rows)}))
    db.commit()
    return Response(
        "\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/operator-options", response_model=ContentOperatorPage)
def operator_options(
    user: Actor,
    db: DB,
    q: str = Query("", max_length=150),
    page: int = Query(1, ge=1, le=2147483647),
):
    from app.scopes import require_global

    require(db, user, "content.manage")
    require_global(user)
    if user.role != "regulator":
        raise HTTPException(403, "仅监管可选择通知企业")
    stmt = select(Operator).where(Operator.enabled.is_(True))
    if q.strip():
        stmt = stmt.where(Operator.name.icontains(q.strip(), autoescape=True))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    return {
        "items": [
            {"id": row.id, "name": row.name}
            for row in db.scalars(stmt.order_by(Operator.id).offset((page - 1) * 20).limit(20))
        ],
        "total": total,
        "page": page,
        "page_size": 20,
    }


@router.get("/{article_id}", response_model=ContentDetail)
def detail(article_id: int, user: SnapshotActor, db: SnapshotDB, manage: bool = False):
    a = content.access(db, user, article_id, manage)
    replies = content.replies_visible(
        select(ArticleReply).where(ArticleReply.article_id == a.id), user
    )
    result = {
        **content.output(a),
        "attachments": [article_assets.output(row) for row in article_assets.records(db, a.id)],
        "replies": [
            {
                "id": r.id,
                "operator_id": r.operator_id,
                "operator_name": db.get(Operator, r.operator_id).name,
                "actor_name": db.get(User, r.actor_id).username,
                "body": r.body,
                "rich_document": r.rich_document,
                "contact_name": r.contact_name,
                "contact_phone": r.contact_phone,
                "created_at": r.created_at.isoformat(),
                "images": [
                    metadata(image)
                    for image in db.scalars(
                        select(ArticleReplyAttachment)
                        .where(ArticleReplyAttachment.reply_id == r.id)
                        .order_by(ArticleReplyAttachment.id)
                    )
                ],
            }
            for r in db.scalars(replies.order_by(ArticleReply.id))
        ],
    }
    result["recipient_ids"] = a.recipient_ids if manage else None
    result["deliveries"] = delivery_outputs(db, a, user, manage)
    if manage:
        result["history"] = [
            {"action": r.action, "snapshot": r.snapshot, "created_at": r.created_at.isoformat()}
            for r in db.scalars(
                select(ArticleAction)
                .where(ArticleAction.article_id == a.id)
                .order_by(ArticleAction.id)
            )
        ]
    return result


@router.patch("/{article_id}", response_model=ContentArticle)
def edit(article_id: int, body: EditInput, request: Request, user: Actor, db: DB):
    authorize_business_write(db, user, request, "content.edit")
    require(db, user, "content.manage")
    content.demo_only()
    if body.portal_public:
        require(db, user, "content.public")
    a = content.access(db, user, article_id, True, True)
    if a.version != body.version or a.status != "draft":
        raise HTTPException(409, "内容已更新或已发布，不能修改")
    if body.operator_id is not None and not db.get(Operator, body.operator_id):
        raise HTTPException(422, "运营商不存在")
    if body.recipient_ids is not None:
        content.recipients(db, body.recipient_ids)
    document = body.rich_document.model_dump(exclude_none=True) if body.rich_document else None
    article_assets.verify_references(db, a.id, document)
    a.rich_document = document
    for key, value in body.model_dump(exclude={"version", "rich_document"}).items():
        setattr(a, key, value)
    a.version += 1
    content.record(db, user, a, "edit")
    result = ContentArticle.model_validate(content.output(a))
    db.commit()
    return result


@router.post("/{article_id}/actions", response_model=ContentArticle)
def action(article_id: int, body: ActionInput, request: Request, user: Actor, db: DB):
    capability = CONTENT_ACTIONS.get(body.action)
    if capability is None:
        raise HTTPException(422, "不支持的内容动作")
    authorize_business_write(db, user, request, capability)
    require(db, user, "content.manage")
    content.demo_only()
    a = content.access(db, user, article_id, True, True)
    content.apply_action(db, user, a, body.action, body.version, body.published_at)
    db.commit()
    return content.output(a)


@router.post("/{article_id}/replies", status_code=201, response_model=ContentReplyCreated)
def reply(article_id: int, body: ReplyInput, request: Request, user: Actor, db: DB):
    authorize_business_write(db, user, request, "content.reply")
    content.demo_only()
    a = content.access(db, user, article_id, False, True)
    if user.role != "operator" or user.operator_id is None or a.kind != "notice":
        raise HTTPException(403, "仅所属企业可回复通知")
    text, document = reply_documents.prepare(body.body, body.rich_document)
    document = reply_documents.bind_images(document, [])
    r = ArticleReply(
        article_id=a.id,
        operator_id=user.operator_id,
        actor_id=user.id,
        body=text,
        rich_document=document,
        contact_name=body.contact_name,
        contact_phone=body.contact_phone,
    )
    db.add(r)
    db.flush()
    result = ContentReplyCreated.model_validate({"id": r.id})
    db.commit()
    return result


def delivery_outputs(db, article, user, manage):
    from app.models import now

    if not manage and user.role != "operator":
        return []
    counts = (
        select(
            ArticleReply.operator_id.label("operator_id"),
            func.count().label("count"),
            func.max(ArticleReply.created_at).label("latest"),
        )
        .where(ArticleReply.article_id == article.id)
        .group_by(ArticleReply.operator_id)
        .subquery()
    )
    stmt = (
        select(NoticeDelivery, counts.c.count, counts.c.latest)
        .outerjoin(counts, counts.c.operator_id == NoticeDelivery.operator_id)
        .where(NoticeDelivery.article_id == article.id)
    )
    if not manage:
        stmt = stmt.where(NoticeDelivery.operator_id == user.operator_id)
    stamp = now()
    return [
        delivery_output(row, article, count, latest, stamp)
        for row, count, latest in db.execute(stmt.order_by(NoticeDelivery.id))
    ]


def delivery_output(row, article, count, latest, stamp):
    state = (
        "withdrawn"
        if article.status == "withdrawn"
        else "scheduled"
        if article.published_at and article.published_at > stamp
        else "visible"
    )
    return {
        "id": row.id,
        "operator_id": row.operator_id,
        "operator_name": row.operator_name,
        "contacts": row.contacts,
        "publication_version": row.publication_version,
        "scheduled_at": row.scheduled_at.isoformat(),
        "created_at": row.created_at.isoformat(),
        "state": state,
        "reply_count": count or 0,
        "latest_reply_at": latest.isoformat() if latest else None,
    }
