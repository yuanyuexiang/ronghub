"""Global audit search; expose only explicitly allowlisted structured details."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.business_audit import STATE_FIELDS, numeric_scores
from app.contracts import AuditPage
from app.dependencies import DB, Actor
from app.models import Audit, User
from app.scopes import require_global

router = APIRouter(tags=["audit"])
SAFE_FIELDS = STATE_FIELDS | {
    "object_type",
    "version",
    "rows",
    "source",
    "target_username",
    "must_change_password",
    "operator_id",
    "user_id",
    "role_profile_id",
    "enabled",
    "archived",
    "revision",
    "allowed_districts",
    "base_role",
    "permissions",
    "start_date",
    "end_date",
    "period",
    "district",
}


def safe_details(raw):
    if not isinstance(raw, dict):
        return {}
    result = {}
    for key, value in raw.items():
        if key == "scores" and isinstance(value, dict):
            result[key] = numeric_scores(value)
        elif (
            (
                key == "decision"
                and isinstance(value, str)
                and value in {"approve", "reject"}
                or key == "count"
                and type(value) is int
                and 1 <= value <= 100
            )
            or key == "application_year"
            and type(value) is int
            and 2000 <= value <= 9998
            or key == "mode"
            and isinstance(value, str)
            and value in {"draft", "submit"}
        ):
            result[key] = value
        elif key == "claims" and isinstance(value, list) and 1 <= len(value) <= 100:
            fields = ("id", "before_version", "after_version")
            if all(
                isinstance(row, dict)
                and all(
                    type(row.get(field)) is int and 1 <= row[field] <= 2147483647
                    for field in fields
                )
                for row in value
            ):
                result[key] = [{field: row[field] for field in fields} for row in value]
        elif key in {"before", "after", "grant_snapshot"} and isinstance(value, dict):
            result[key] = safe_details(value)
        elif key in SAFE_FIELDS and (
            value is None
            or isinstance(value, (str, bool, int, float))
            or (isinstance(value, list) and all(isinstance(v, str) for v in value))
        ):
            result[key] = value
    return result


@router.get("/api/v1/audits", response_model=AuditPage)
def search(
    user: Actor,
    db: DB,
    actor: str = Query("", max_length=100),
    action: str = Query("", max_length=100),
    object_id: int | None = Query(None, ge=1),
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if user.role != "regulator":
        raise HTTPException(403, "仅监管授权人员可检索操作日志")
    # Existing audit events have heterogeneous object IDs and no reliable district snapshot.
    # Never infer geography from the actor's current account or a coincident object ID.
    require_global(user)
    for value in [start_at, end_at]:
        if value is not None and value.utcoffset() is None:
            raise HTTPException(422, "日志时间必须包含时区")
    if start_at and end_at and start_at >= end_at:
        raise HTTPException(422, "结束时间须晚于开始时间")
    stmt = select(Audit, User.username).join(User, User.id == Audit.user_id)
    if actor.strip():
        stmt = stmt.where(User.username == actor.strip())
    if action.strip():
        stmt = stmt.where(Audit.action == action.strip())
    if object_id is not None:
        stmt = stmt.where(Audit.object_id == object_id)
    if start_at:
        stmt = stmt.where(Audit.created_at >= start_at)
    if end_at:
        stmt = stmt.where(Audit.created_at < end_at)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(
        stmt.order_by(Audit.created_at.desc(), Audit.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [
            {
                "id": record.id,
                "actor": username,
                "action": record.action,
                "object_id": record.object_id,
                "created_at": record.created_at.isoformat(),
                "result": "committed",
                "detail": safe_details(record.detail),
            }
            for record, username in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "notice": "仅展示已记录并提交成功的审计事件；新增关键业务留痕不回填旧历史，不含全部附件操作、登录失败或完整访问日志。详情采用字段白名单。",
    }
