from datetime import date
from decimal import Decimal
from itertools import pairwise

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app import rating
from app.authorization import RATING_ACTIONS, authorize_business_write
from app.business_contracts import (
    RatingApplicationDetail,
    RatingApplicationResponse,
    RatingFieldList,
    RatingStandardList,
    RatingStandardResponse,
)
from app.business_dates import Dates, apply_dates, submission_time
from app.contracts import RatingQueuePage
from app.dependencies import DB, Actor, SnapshotActor
from app.models import (
    Operator,
    RatingAction,
    RatingApplication,
    RatingStandard,
    Station,
    User,
)
from app.rating_configuration import FIELD_CATALOG, IndicatorInput
from app.rating_results import local_day
from app.reporting import SnapshotDB
from app.scopes import records as scoped_records

router = APIRouter(prefix="/api/v1/ratings", tags=["ratings"])


class CategoryInput(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    weight: Decimal = Field(gt=0, max_digits=10, decimal_places=4)


class ThresholdInput(BaseModel):
    minimum: Decimal = Field(ge=0, le=100, decimal_places=2)
    stars: int = Field(ge=1, le=5)


class StandardInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=60)
    version: int = Field(ge=1, le=2147483647)
    name: str = Field(min_length=1, max_length=150)
    categories: list[CategoryInput] = Field(default_factory=list, max_length=20)
    indicators: list[IndicatorInput] = Field(min_length=1, max_length=50)
    thresholds: list[ThresholdInput] = Field(min_length=1, max_length=5)

    @field_validator("version", mode="before")
    @classmethod
    def no_boolean_version(cls, value):
        if isinstance(value, bool):
            raise ValueError("业务标准版本必须为正整数")  # noqa: TRY004 - Pydantic validators need ValueError for 422
        return value

    @model_validator(mode="after")
    def validate_rules(self):
        if any(
            not value.strip() or "\x00" in value
            for value in [
                self.code,
                self.name,
                *[text for category in self.categories for text in (category.code, category.name)],
            ]
        ):
            raise ValueError("标准和分类文字须非空且不能包含NUL")
        if len({i.code for i in self.indicators}) != len(self.indicators):
            raise ValueError("指标代码不能重复")
        if any(i.source not in {"manual", "runtime"} for i in self.indicators):
            raise ValueError("不支持的指标来源")
        if self.categories:
            known = {c.code for c in self.categories}
            if len(known) != len(self.categories) or {i.category for i in self.indicators} != known:
                raise ValueError("分类代码应唯一且每个分类必须有指标，指标也必须属于已配置分类")
        if not any(item.enabled for item in self.indicators):
            raise ValueError("至少保留一个启用指标")
        if self.categories and any(
            not any(item.enabled and item.category == category.code for item in self.indicators)
            for category in self.categories
        ):
            raise ValueError("每个分类必须至少保留一个启用指标")
        thresholds = sorted(self.thresholds, key=lambda t: t.minimum)
        if len({t.minimum for t in thresholds}) != len(thresholds) or any(
            a.stars >= b.stars for a, b in pairwise(thresholds)
        ):
            raise ValueError("星级门槛及级别必须严格递增")
        return self


class ApplicationInput(BaseModel):
    station_id: int
    kind: str = "initial"
    parent_id: int | None = None
    parent_version: int | None = None
    standard_id: int
    scores: dict[str, str | None] = Field(default_factory=dict, max_length=50)
    evidence_note: str = Field(default="", max_length=2000)


class EditInput(BaseModel):
    version: int = Field(ge=1)
    scores: dict[str, str | None] = Field(default_factory=dict, max_length=50)
    evidence_note: str = Field(default="", max_length=2000)


class ActionInput(BaseModel):
    valid_until: date | None = None
    action: str
    version: int
    comment: str = Field(default="", max_length=2000)
    scores: dict[str, str | None] = Field(default_factory=dict, max_length=50)


def standard_output(s):
    dates = [
        item.get("effective_from")
        for item in s.rules.get("indicators", [])
        if s.rules.get("indicator_status_version") != 1 or item.get("enabled") is True
    ]
    effective_on = (
        max(dates) if dates and all(dates) and s.rules.get("configuration_version") == 2 else None
    )
    activation_state = (
        "legacy_unknown"
        if effective_on is None
        else "scheduled"
        if date.fromisoformat(effective_on) > local_day()
        else "effective"
    )
    return {
        "revision": s.revision,
        "archived": s.archived,
        "effective_on": effective_on,
        "activation_state": activation_state,
        "id": s.id,
        "code": s.code,
        "version": s.version,
        "name": s.name,
        "rules": s.rules,
        "is_demo": s.is_demo,
        "enabled": s.enabled,
    }


@router.get("/fields", response_model=RatingFieldList)
def fields(user: Actor):
    return {"items": FIELD_CATALOG}


@router.get("/standards", response_model=RatingStandardList)
def standards(user: Actor, db: DB, include_archived: bool = False):
    if user.role not in {"operator", "association", "regulator"}:
        raise HTTPException(403, "无权查看评价标准")
    from app.standard_lifecycle import manager

    if include_archived:
        manager(db, user)
    stmt = select(RatingStandard).order_by(RatingStandard.id.desc())
    if not include_archived:
        stmt = stmt.where(RatingStandard.archived.is_(False))
    return {"items": [standard_output(row) for row in db.scalars(stmt)]}


@router.post("/standards", status_code=201, response_model=RatingStandardResponse)
def create_standard(body: StandardInput, user: Actor, db: DB, request: Request):
    authorize_business_write(db, user, request, "ratings.standards")
    rating.demo_only()
    if user.role != "association":
        raise HTTPException(403, "仅协会人员可制定标准")
    from app.standard_lifecycle import create_standard as persist_standard

    try:
        s = persist_standard(db, user, body)
        result = RatingStandardResponse.model_validate(standard_output(s))
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, "标准代码及版本已存在") from e
    return result


def applications_statement(user, q="", status="", record_id=None, kind="", dates=None):
    if status not in {
        "",
        "draft",
        "submitted",
        "reviewed",
        "published",
        "rejected",
        "withdrawn",
        "delisted",
        "superseded",
    }:
        raise HTTPException(422, "不支持的办理状态")
    if kind not in {"", "initial", "renewal", "reassessment", "appeal"}:
        raise HTTPException(422, "不支持的评价类型")
    stmt = scoped_records(
        select(
            RatingApplication,
            Station.name,
            Operator.name,
            Station.status,
            submission_time(RatingApplication).label("last_submitted_at"),
        )
        .join(Station, Station.id == RatingApplication.station_id)
        .join(Operator, Operator.id == RatingApplication.operator_id),
        RatingApplication,
        user,
    )
    if user.role == "operator" and user.operator_id is not None:
        stmt = stmt.where(RatingApplication.operator_id == user.operator_id)
    elif user.role not in {"association", "regulator"}:
        raise HTTPException(403, "无权查看申报记录")
    if q.strip():
        stmt = stmt.where(
            or_(
                Station.name.icontains(q.strip(), autoescape=True),
                Operator.name.icontains(q.strip(), autoescape=True),
            )
        )
    if status:
        stmt = stmt.where(RatingApplication.status == status)
    if kind:
        stmt = stmt.where(RatingApplication.kind == kind)
    if record_id is not None:
        stmt = stmt.where(RatingApplication.id == record_id)
    return apply_dates(stmt, RatingApplication, dates)


@router.get("/applications", response_model=RatingQueuePage)
def applications(
    user: SnapshotActor,
    db: SnapshotDB,
    dates: Dates,
    q: str = Query("", max_length=150),
    status: str = Query("", max_length=30),
    kind: str = Query("", max_length=30),
    record_id: int | None = Query(None, ge=1, le=9223372036854775807),
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    stmt = applications_statement(user, q, status, record_id, kind, dates)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(
        stmt.order_by(RatingApplication.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return {
        "items": [
            {
                **rating.output(c, station_status),
                "station_name": station_name,
                "operator_name": operator_name,
                "last_submitted_at": submitted_at.isoformat() if submitted_at else None,
            }
            for c, station_name, operator_name, station_status, submitted_at in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/applications", status_code=201, response_model=RatingApplicationResponse)
def create_application(body: ApplicationInput, user: Actor, db: DB, request: Request):
    authorize_business_write(db, user, request, "ratings.create")
    a = rating.create(
        db,
        user,
        body.station_id,
        body.standard_id,
        body.scores,
        body.evidence_note,
        body.kind,
        body.parent_id,
        body.parent_version,
    )
    result = RatingApplicationResponse.model_validate(
        rating.output(a, db.get(Station, a.station_id).status)
    )
    db.commit()
    return result


@router.get("/applications/{application_id}", response_model=RatingApplicationDetail)
def detail(application_id: int, user: SnapshotActor, db: SnapshotDB):
    a = rating.access(db, user, application_id)
    station_name, station_status, operator_name = db.execute(
        select(Station.name, Station.status, Operator.name)
        .select_from(Station)
        .join(Operator, Operator.id == a.operator_id)
        .where(Station.id == a.station_id)
    ).one()
    history = list(
        db.scalars(
            select(RatingAction)
            .where(RatingAction.application_id == a.id)
            .order_by(RatingAction.id)
        )
    )
    # Like the queue's submission_time: latest action ID, never max(timestamp) or creation.
    submitted = next((action for action in reversed(history) if action.action == "submit"), None)
    receipt = None
    if submitted:
        receipt = {
            "action_id": submitted.id,
            "actor_id": submitted.actor_id,
            "current_account_username": db.scalar(
                select(User.username).where(User.id == submitted.actor_id)
            ),
            "submitted_at": submitted.created_at.isoformat(),
        }
    return {
        **rating.output(a, station_status),
        "station_name": station_name,
        "operator_name": operator_name,
        "last_submission": receipt,
        "rule_snapshot": a.rule_snapshot,
        "initial_scores": a.initial_scores,
        "current_scores": a.current_scores,
        "history": [
            {
                "action": r.action,
                "comment": r.comment,
                "payload": r.payload,
                "created_at": r.created_at.isoformat(),
            }
            for r in history
        ],
    }


@router.post("/applications/{application_id}/actions", response_model=RatingApplicationResponse)
def action(application_id: int, body: ActionInput, user: Actor, db: DB, request: Request):
    capability = RATING_ACTIONS.get(body.action)
    if capability is None:
        raise HTTPException(422, "不支持的评价动作")
    authorize_business_write(db, user, request, capability)
    a = rating.act(
        db,
        user,
        application_id,
        body.action,
        body.version,
        body.comment,
        body.scores,
        body.valid_until,
    )
    result = RatingApplicationResponse.model_validate(
        rating.output(a, db.get(Station, a.station_id).status)
    )
    db.commit()
    return result


@router.patch("/applications/{application_id}", response_model=RatingApplicationResponse)
def edit_application(application_id: int, body: EditInput, user: Actor, db: DB, request: Request):
    authorize_business_write(db, user, request, "ratings.edit")
    a = rating.edit(db, user, application_id, body.version, body.scores, body.evidence_note)
    result = RatingApplicationResponse.model_validate(
        rating.output(a, db.get(Station, a.station_id).status)
    )
    db.commit()
    return result
