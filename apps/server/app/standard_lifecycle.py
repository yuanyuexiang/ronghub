"""Global association standard management with immutable final versions."""

from fastapi import HTTPException
from sqlalchemy import select

from app.authorization import require
from app.models import Audit, RatingStandard, RatingStandardDraft
from app.scopes import require_global


def manager(db, user):
    require(db, user, "ratings.read")
    require(db, user, "ratings.standards")
    require_global(user)
    if user.role != "association":
        raise HTTPException(403, "仅全域协会标准管理人员可办理")


def create_standard(db, user, body):
    data = body.model_dump(mode="json")
    rules = {
        "configuration_version": 2,
        "indicator_status_version": 1,
        **{key: data.pop(key) for key in ("categories", "indicators", "thresholds")},
    }
    standard = RatingStandard(**data, rules=rules, is_demo=True)
    db.add(standard)
    db.flush()
    db.add(
        Audit(
            user_id=user.id,
            action="ratings.standard_create",
            object_id=standard.id,
            detail={
                "object_type": "rating_standard",
                "after": {"id": standard.id, "version": standard.version},
            },
        )
    )
    return standard


def draft_output(row, detail=False):
    result = {
        "id": row.id,
        "version": row.version,
        "status": row.status,
        "code": row.data.get("code", ""),
        "name": row.data.get("name", ""),
        "published_standard_id": row.published_standard_id,
        "created_by": row.created_by,
        "updated_by": row.updated_by,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }
    if detail:
        result["data"] = row.data
    return result


def draft_access(db, draft_id, lock=False):
    stmt = select(RatingStandardDraft).where(RatingStandardDraft.id == draft_id)
    row = db.scalar(stmt.with_for_update() if lock else stmt)
    if not row:
        raise HTTPException(404, "标准草稿不存在")
    return row


def writable(row, version):
    if row.version != version:
        raise HTTPException(409, "草稿版本已变化，请保留输入并重新核对")
    if row.status != "draft":
        raise HTTPException(409, "只有未定稿且未删除的草稿可以修改")


def audit(db, user, action, object_id, before, after, object_type="rating_standard_draft"):
    db.add(
        Audit(
            user_id=user.id,
            action=action,
            object_id=object_id,
            detail={"object_type": object_type, "before": before, "after": after},
        )
    )
