"""Recoverable drafts, finalization and reference-safe availability management."""

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app import rating
from app import standard_lifecycle as lifecycle
from app.authorization import authorize_business_write
from app.business_contracts import RatingStandardResponse, RatingWeightPlan
from app.dependencies import DB, Actor, SnapshotActor
from app.models import RatingApplication, RatingStandard, RatingStandardDraft, now
from app.rating_draft_contracts import (
    Id,
    RatingDraftCreate,
    RatingDraftDetail,
    RatingDraftPage,
    RatingDraftUpdate,
    RatingDraftVersion,
)
from app.reporting import SnapshotDB
from app.routers.rating import StandardInput, standard_output

PathId = Annotated[int, Field(ge=1, le=2147483647)]

router = APIRouter(prefix="/api/v1/ratings", tags=["ratings"])


def writing(db, user, request):
    authorize_business_write(db, user, request, "ratings.standards")
    lifecycle.manager(db, user)
    rating.demo_only()


@router.get("/standard-drafts", response_model=RatingDraftPage)
def listing(
    user: SnapshotActor,
    db: SnapshotDB,
    status: Literal["draft", "published", "deleted"] = "draft",
    q: str = Query("", max_length=150),
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    lifecycle.manager(db, user)
    stmt = select(RatingStandardDraft).where(RatingStandardDraft.status == status)
    if q.strip():
        stmt = stmt.where(
            or_(
                RatingStandardDraft.data["name"].as_string().icontains(q.strip(), autoescape=True),
                RatingStandardDraft.data["code"].as_string().icontains(q.strip(), autoescape=True),
            )
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(
        stmt.order_by(RatingStandardDraft.updated_at.desc(), RatingStandardDraft.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [lifecycle.draft_output(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/standard-drafts", status_code=201, response_model=RatingDraftDetail)
def create(body: RatingDraftCreate, user: Actor, db: DB, request: Request):
    writing(db, user, request)
    row = RatingStandardDraft(
        data=body.data.model_dump(mode="json"), created_by=user.id, updated_by=user.id
    )
    db.add(row)
    db.flush()
    lifecycle.audit(
        db,
        user,
        "ratings.standard_draft_create",
        row.id,
        None,
        {"status": row.status, "version": row.version},
    )
    result = RatingDraftDetail.model_validate(lifecycle.draft_output(row, True))
    db.commit()
    return result


@router.get("/standard-drafts/{draft_id}", response_model=RatingDraftDetail)
def detail(draft_id: PathId, user: Actor, db: DB):
    lifecycle.manager(db, user)
    return lifecycle.draft_output(lifecycle.draft_access(db, draft_id), True)


@router.patch("/standard-drafts/{draft_id}", response_model=RatingDraftDetail)
def edit(draft_id: PathId, body: RatingDraftUpdate, user: Actor, db: DB, request: Request):
    writing(db, user, request)
    row = lifecycle.draft_access(db, draft_id, True)
    lifecycle.writable(row, body.version)
    before = {"status": row.status, "version": row.version}
    row.data = body.data.model_dump(mode="json")
    row.version += 1
    row.updated_by = user.id
    row.updated_at = now()
    lifecycle.audit(
        db,
        user,
        "ratings.standard_draft_edit",
        row.id,
        before,
        {"status": row.status, "version": row.version},
    )
    result = RatingDraftDetail.model_validate(lifecycle.draft_output(row, True))
    db.commit()
    return result


@router.delete("/standard-drafts/{draft_id}", response_model=RatingDraftDetail)
def remove(
    draft_id: PathId,
    user: Actor,
    db: DB,
    request: Request,
    version: int = Query(ge=1, le=2147483647),
):
    writing(db, user, request)
    row = lifecycle.draft_access(db, draft_id, True)
    lifecycle.writable(row, version)
    before = {"status": row.status, "version": row.version}
    row.status = "deleted"
    row.version += 1
    row.updated_by = user.id
    row.updated_at = now()
    lifecycle.audit(
        db,
        user,
        "ratings.standard_draft_delete",
        row.id,
        before,
        {"status": row.status, "version": row.version},
    )
    result = RatingDraftDetail.model_validate(lifecycle.draft_output(row, True))
    db.commit()
    return result


class Finalized(BaseModel):
    draft: RatingDraftDetail
    standard: RatingStandardResponse


@router.post("/standard-drafts/{draft_id}/finalize", response_model=Finalized)
def finalize(draft_id: PathId, body: RatingDraftVersion, user: Actor, db: DB, request: Request):
    writing(db, user, request)
    row = lifecycle.draft_access(db, draft_id, True)
    lifecycle.writable(row, body.version)
    try:
        validated = StandardInput.model_validate(row.data)
    except ValidationError as error:
        issue = error.errors()[0]
        field = ".".join(str(part) for part in issue["loc"])
        raise HTTPException(422, f"草稿尚未完整：{field[:100]}，{issue['msg'][:200]}") from error
    try:
        standard = lifecycle.create_standard(db, user, validated)
        before = {"status": row.status, "version": row.version}
        row.status = "published"
        row.version += 1
        row.updated_by = user.id
        row.updated_at = now()
        row.published_standard_id = standard.id
        lifecycle.audit(
            db,
            user,
            "ratings.standard_draft_finalize",
            row.id,
            before,
            {"status": row.status, "version": row.version},
        )
        result = Finalized.model_validate(
            {"draft": lifecycle.draft_output(row, True), "standard": standard_output(standard)}
        )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409, "标准代码与版本已存在，草稿已保留，请修改后重试") from error
    return result


class Availability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: Id
    enabled: StrictBool


def standard_access(db, standard_id, revision):
    row = db.scalar(
        select(RatingStandard).where(RatingStandard.id == standard_id).with_for_update()
    )
    if not row or not row.is_demo:
        raise HTTPException(404, "演示标准不存在")
    if row.revision != revision:
        raise HTTPException(409, "标准管理版本已变化，请重新核对")
    if row.archived:
        raise HTTPException(409, "已删除归档的标准不能继续办理")
    return row


@router.post("/standards/preview", response_model=RatingWeightPlan)
def preview(body: StandardInput, user: Actor, db: DB):
    from app.rating_configuration import weight_plan

    lifecycle.manager(db, user)
    rating.demo_only()
    data = body.model_dump(mode="json")
    return weight_plan(
        {"indicator_status_version": 1, **{key: data[key] for key in ("categories", "indicators")}}
    )


@router.get("/standards/{standard_id}", response_model=RatingStandardResponse)
def standard_detail(standard_id: PathId, user: Actor, db: DB):
    row = db.get(RatingStandard, standard_id)
    if not row:
        raise HTTPException(404, "标准不存在")
    if row.archived:
        lifecycle.manager(db, user)
    return standard_output(row)


@router.post("/standards/{standard_id}/availability", response_model=RatingStandardResponse)
def availability(standard_id: PathId, body: Availability, user: Actor, db: DB, request: Request):
    writing(db, user, request)
    row = standard_access(db, standard_id, body.revision)
    if row.enabled == body.enabled:
        raise HTTPException(409, "标准已处于目标状态，请刷新核对")
    before = {"enabled": row.enabled, "archived": row.archived, "revision": row.revision}
    row.enabled = body.enabled
    row.revision += 1
    lifecycle.audit(
        db,
        user,
        "ratings.standard_availability",
        row.id,
        before,
        {"enabled": row.enabled, "archived": row.archived, "revision": row.revision},
        "rating_standard",
    )
    result = RatingStandardResponse.model_validate(standard_output(row))
    db.commit()
    return result


@router.delete("/standards/{standard_id}", response_model=RatingStandardResponse)
def archive(
    standard_id: PathId,
    user: Actor,
    db: DB,
    request: Request,
    revision: int = Query(ge=1, le=2147483647),
):
    writing(db, user, request)
    row = standard_access(db, standard_id, revision)
    if db.scalar(
        select(RatingApplication.id).where(RatingApplication.standard_id == row.id).limit(1)
    ):
        raise HTTPException(409, "标准已被申请引用，不能删除；可停用以阻止新申请")
    before = {"enabled": row.enabled, "archived": row.archived, "revision": row.revision}
    row.enabled = False
    row.archived = True
    row.revision += 1
    lifecycle.audit(
        db,
        user,
        "ratings.standard_archive",
        row.id,
        before,
        {"enabled": row.enabled, "archived": row.archived, "revision": row.revision},
        "rating_standard",
    )
    result = RatingStandardResponse.model_validate(standard_output(row))
    db.commit()
    return result
