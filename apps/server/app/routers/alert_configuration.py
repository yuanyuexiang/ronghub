"""Immutable multi-level rule versions, explicit lifecycle and side-effect-free preview."""

from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from app import alerts
from app.alert_configuration import (
    UNITS,
    Configuration,
    Kind,
    Preview,
    RuleControl,
    RuleCreate,
)
from app.alert_period_worker import rule_lock
from app.authorization import authorize_business_write, require
from app.decimal_contracts import DecimalText
from app.dependencies import DB, Actor, SnapshotActor
from app.models import AlertRule, Audit, User, now
from app.reporting import SnapshotDB
from app.scopes import require_global

router = APIRouter(prefix="/api/v1/alerts/configured-rules", tags=["configured alerts"])


class RuleResponse(BaseModel):
    id: int
    code: str
    version: int
    name: str
    configuration: Configuration
    unit: str
    notifications_enabled: bool
    enabled: bool
    archived: bool
    revision: int
    created_by: int | None
    created_by_name: str | None
    updated_by: int | None
    updated_by_name: str | None
    created_at: str | None
    updated_at: str | None
    is_demo: bool
    automatic_status: Literal[
        "instant_manual", "disabled", "superseded", "missing_start", "eligible"
    ]
    monitor_from: str | None
    execution_supported: bool


class RulePage(BaseModel):
    items: list[RuleResponse]
    total: int
    page: int
    page_size: int


class PreviewResponse(BaseModel):
    verdict: Literal["unknown", "normal", "alert"]
    level: Literal["general", "severe", "critical"] | None
    comparison_value: DecimalText | None
    unit: str
    is_demo: bool
    notice: str


def manager(user, db):
    if user.role != "regulator":
        raise HTTPException(403, "仅监管人员可维护全域告警配置")
    require(db, user, "alerts.read")
    require(db, user, "alerts.rules")
    require_global(user)


def output(db, rule, creator=None, updater=None):
    latest = db.scalar(select(func.max(AlertRule.version)).where(AlertRule.code == rule.code))
    automatic_status = (
        "instant_manual"
        if rule.configuration["period"] == "instant"
        else "superseded"
        if latest and latest > rule.version
        else "disabled"
        if not rule.enabled or rule.archived
        else "missing_start"
        if rule.monitor_from is None
        else "eligible"
    )
    return RuleResponse(
        id=rule.id,
        code=rule.code,
        version=rule.version,
        name=rule.name,
        configuration=rule.configuration,
        unit=rule.unit,
        notifications_enabled=rule.notifications_enabled,
        enabled=rule.enabled,
        archived=rule.archived,
        revision=rule.revision,
        created_by=rule.created_by,
        updated_by=rule.updated_by,
        created_by_name=creator,
        updated_by_name=updater,
        created_at=rule.created_at.isoformat() if rule.created_at else None,
        updated_at=rule.updated_at.isoformat() if rule.updated_at else None,
        is_demo=rule.is_demo,
        automatic_status=automatic_status,
        monitor_from=rule.monitor_from.isoformat() if rule.monitor_from else None,
        execution_supported=True,
    )


def audit(db, user, action, rule, before, after):
    db.add(
        Audit(
            user_id=user.id,
            action=action,
            object_id=rule.id,
            detail={"before": before, "after": after.model_dump(mode="json")},
        )
    )


@router.get("", response_model=RulePage)
def listing(
    user: SnapshotActor,
    db: SnapshotDB,
    response: Response,
    q: str = Query("", max_length=150),
    kind: Kind | None = None,
    kind_group: Literal["station", "device"] | None = None,
    enabled: bool | None = None,
    include_archived: bool = False,
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    alerts_role(user)
    if include_archived:
        manager(user, db)
    creator, updater = aliased(User), aliased(User)
    stmt = (
        select(AlertRule, creator.username, updater.username)
        .outerjoin(creator, creator.id == AlertRule.created_by)
        .outerjoin(updater, updater.id == AlertRule.updated_by)
        .where(AlertRule.configuration.is_not(None))
    )
    if not include_archived:
        stmt = stmt.where(AlertRule.archived.is_(False))
    if q:
        stmt = stmt.where(
            AlertRule.name.contains(q, autoescape=True)
            | AlertRule.code.contains(q, autoescape=True)
        )
    if kind_group:
        kinds = (
            ["station_utilization", "station_fault", "station_offline"]
            if kind_group == "station"
            else ["device_temperature", "device_current", "device_leakage"]
        )
        stmt = stmt.where(AlertRule.configuration["kind"].as_string().in_(kinds))
    if kind:
        stmt = stmt.where(AlertRule.configuration["kind"].as_string() == kind)
    if enabled is not None:
        stmt = stmt.where(AlertRule.enabled == enabled)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(
        stmt.order_by(AlertRule.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    result = RulePage(
        items=[output(db, r, c, u) for r, c, u in rows], total=total, page=page, page_size=page_size
    )
    response.headers["Cache-Control"] = "no-store"
    return result


def alerts_role(user):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "无权查看告警配置")


@router.post("/preview", response_model=PreviewResponse)
def preview(body: Preview, user: Actor, db: DB, response: Response):
    manager(user, db)
    response.headers["Cache-Control"] = "no-store"
    unit = UNITS[body.configuration.kind]
    if not body.coverage_complete:
        return PreviewResponse(
            verdict="unknown",
            level=None,
            comparison_value=None,
            unit=unit,
            is_demo=True,
            notice="覆盖未完整；不按正常或零值判断，不保存或生成告警。",
        )
    with localcontext() as context:
        context.prec = 50
        value = (
            body.value * 100 / body.rated_current_a
            if body.configuration.kind == "device_current"
            else body.value
        )
        level = body.configuration.level(value)
        shown = format(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP), "f")
    return PreviewResponse(
        verdict="alert" if level else "normal",
        level=level,
        comparison_value=shown,
        unit=unit,
        is_demo=True,
        notice="仅校验当前输入的演示配置；按未舍入原值比较，不保存观测或生成告警。",
    )


@router.post("", response_model=RuleResponse, status_code=201)
def create(body: RuleCreate, request: Request, user: Actor, db: DB, response: Response):
    authorize_business_write(db, user, request, "alerts.rules")
    manager(user, db)
    alerts.demo_only()
    rule_lock(db)
    c = body.configuration
    stamp = now()
    rule = AlertRule(
        code=body.code,
        version=body.version,
        name=body.name,
        configuration=c.model_dump(mode="json"),
        metric=c.kind,
        unit=UNITS[c.kind],
        trigger_value=c.trigger,
        recover_value=c.recover_value,
        notifications_enabled=body.notifications_enabled,
        is_demo=True,
        enabled=True,
        revision=1,
        archived=False,
        created_by=user.id,
        updated_by=user.id,
        monitor_from=stamp if c.period != "instant" else None,
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(rule)
    try:
        db.flush()
        result = output(db, rule, user.username, user.username)
        audit(db, user, "alerts.configuration.create", rule, None, result)
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409, "规则代码与版本已存在，原版本未覆盖") from error
    response.headers["Cache-Control"] = "no-store"
    return result


def locked(db, user, rule_id, revision):
    rule_lock(db)
    rule = db.scalar(
        select(AlertRule)
        .where(AlertRule.id == rule_id, AlertRule.configuration.is_not(None))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not rule:
        raise HTTPException(404, "配置不存在")
    if rule.revision != revision:
        raise HTTPException(409, "配置状态已变化，请刷新后重新核对")
    if rule.archived:
        raise HTTPException(409, "配置已归档，不能再次修改")
    creator = db.get(User, rule.created_by)
    updater = db.get(User, rule.updated_by)
    return rule, output(
        db, rule, creator.username if creator else None, updater.username if updater else None
    )


@router.patch("/{rule_id}/availability", response_model=RuleResponse)
def availability(
    rule_id: int, body: RuleControl, request: Request, user: Actor, db: DB, response: Response
):
    authorize_business_write(db, user, request, "alerts.rules")
    manager(user, db)
    alerts.demo_only()
    rule, before = locked(db, user, rule_id, body.revision)
    if body.enabled and not rule.enabled and rule.configuration["period"] != "instant":
        rule.monitor_from = now()
    rule.enabled = body.enabled
    rule.revision += 1
    rule.updated_by = user.id
    rule.updated_at = now()
    result = output(db, rule, before.created_by_name, user.username)
    audit(
        db, user, "alerts.configuration.availability", rule, before.model_dump(mode="json"), result
    )
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.delete("/{rule_id}", response_model=RuleResponse)
def archive(rule_id: int, revision: int, request: Request, user: Actor, db: DB, response: Response):
    authorize_business_write(db, user, request, "alerts.rules")
    manager(user, db)
    alerts.demo_only()
    rule, before = locked(db, user, rule_id, revision)
    rule.archived = True
    rule.enabled = False
    rule.revision += 1
    rule.updated_by = user.id
    rule.updated_at = now()
    result = output(db, rule, before.created_by_name, user.username)
    audit(db, user, "alerts.configuration.archive", rule, before.model_dump(mode="json"), result)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result
