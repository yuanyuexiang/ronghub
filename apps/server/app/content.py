"""Structured and legacy text publication with private assets and scheduled visibility."""

from fastapi import HTTPException
from sqlalchemy import false, or_, select

from app.business_audit import transition
from app.config import settings
from app.models import Article, ArticleAction, ArticleReply, NoticeDelivery, Operator, now
from app.scopes import company_scope


def demo_only():
    if not settings.demo_mode:
        raise HTTPException(409, "内容发布写入暂限演示环境")


def visible(stmt, user, manage=False):
    if user.role not in {"regulator", "association", "operator"}:
        raise HTTPException(403, "无权查看内容")
    if manage:
        if user.role != "regulator":
            raise HTTPException(403, "无权管理内容")
        return stmt
    stmt = stmt.where(
        Article.status == "published",
        Article.published_at <= now(),
        Article.audience.in_(["all", user.role]),
    )
    legacy = Article.recipient_ids.is_(None) & or_(
        Article.operator_id.is_(None), Article.operator_id == user.operator_id
    )
    explicit = (
        select(NoticeDelivery.id)
        .where(
            NoticeDelivery.article_id == Article.id, NoticeDelivery.operator_id == user.operator_id
        )
        .correlate(Article)
        .exists()
    )
    return stmt.where(or_(legacy, explicit if user.role == "operator" else false()))


def access(db, user, article_id, manage=False, lock=False):
    stmt = visible(select(Article).where(Article.id == article_id), user, manage)
    article = db.scalar(stmt.with_for_update() if lock else stmt)
    if not article:
        raise HTTPException(404, "内容不存在或不可访问")
    return article


def output(a):
    return {
        "id": a.id,
        "kind": a.kind,
        "title": a.title,
        "body": a.body,
        "rich_document": a.rich_document,
        "audience": a.audience,
        "operator_id": a.operator_id,
        "status": a.status,
        "published_at": a.published_at.isoformat() if a.published_at else None,
        "version": a.version,
        "is_demo": a.is_demo,
        "created_at": a.created_at.isoformat(),
    }


def record(db, user, a, action):
    before = (
        db.scalar(
            select(ArticleAction.snapshot)
            .where(ArticleAction.article_id == a.id)
            .order_by(ArticleAction.id.desc())
            .limit(1)
        )
        if action != "create"
        else None
    )
    db.flush()
    from app import article_assets

    snapshot = {
        **output(a),
        "recipient_ids": a.recipient_ids,
        "attachments": [article_assets.output(row) for row in article_assets.records(db, a.id)],
    }
    transition(db, user, "content." + action, a.id, before, snapshot, "article")
    db.add(ArticleAction(article_id=a.id, actor_id=user.id, action=action, snapshot=snapshot))


def replies_visible(stmt, user):
    if user.role == "regulator":
        return stmt.where(ArticleReply.operator_id.in_(company_scope(select(Operator.id), user)))
    if user.role == "operator":
        return stmt.where(ArticleReply.operator_id == user.operator_id)
    return stmt.where(false())


def reply_contact(name: str | None, phone: str | None):
    """Normalize explicitly supplied reply-time contact data; never infer historical values."""
    name, phone = (name or "").strip() or None, (phone or "").strip() or None
    if bool(name) != bool(phone):
        raise HTTPException(422, "回复联系人和联系方式需同时填写")
    if (name and len(name) > 100) or (phone and len(phone) > 80):
        raise HTTPException(422, "回复联系人或联系方式过长")
    if any(not char.isprintable() for value in (name, phone) if value for char in value):
        raise HTTPException(422, "联系人和联系方式不能包含控制字符")
    return name, phone


def recipients(db, ids):
    """Lock in stable ID order while freezing publication contacts."""
    rows = list(
        db.scalars(
            select(Operator)
            .where(Operator.id.in_(ids))
            .order_by(Operator.id)
            .with_for_update(read=True)
        )
    )
    if len(rows) != len(ids) or any(not row.enabled for row in rows):
        raise HTTPException(422, "通知收件企业不存在或已停用，请重新选择")
    return rows


def freeze_recipients(db, article):
    if article.recipient_ids is None:
        return
    for row in recipients(db, article.recipient_ids):
        db.add(
            NoticeDelivery(
                article_id=article.id,
                operator_id=row.id,
                operator_name=row.name,
                contacts=[
                    {key: c.get(key) for key in ("name", "phone", "email", "position", "primary")}
                    for c in row.contacts
                ],
                publication_version=article.version + 1,
                scheduled_at=article.published_at,
            )
        )


def apply_action(db, user, article, action, version, published_at=None):
    """Caller owns authorization and row locks; commit belongs to the whole operation."""
    from app import article_assets

    if article.version != version:
        raise HTTPException(409, f"内容 #{article.id} 版本已更新，请刷新")
    if action == "publish" and article.status == "draft":
        stamp = published_at or now()
        if stamp.tzinfo is None:
            raise HTTPException(422, "发布时间必须包含时区")
        article_assets.verify_references(db, article.id, article.rich_document)
        for asset in article_assets.records(db, article.id):
            article_assets.verified_path(asset)
            asset.frozen = True
        article.published_at, article.status = stamp, "published"
        freeze_recipients(db, article)
    elif action == "withdraw" and article.status == "published":
        article.status = "withdrawn"
    elif action == "delete" and article.status == "draft":
        article.status = "deleted"
    else:
        raise HTTPException(409, f"内容 #{article.id} 当前状态不支持此操作")
    article.version += 1
    record(db, user, article, action)
