from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app import alerts
from app.authorization import authorize_business_write
from app.business_audit import state, transition
from app.contracts import AlertQueuePage
from app.dependencies import DB, Actor
from app.models import AlertEvent, AlertNotice, AlertRule, Operator, Station, now
from app.scopes import records as scoped_records

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


class RuleInput(BaseModel):
    code: str = Field(min_length=1, max_length=60)
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=150)
    metric: str = Field(min_length=1, max_length=60)
    unit: str = Field(min_length=1, max_length=30)
    trigger_value: Decimal = Field(max_digits=18, decimal_places=6)
    recover_value: Decimal = Field(max_digits=18, decimal_places=6)
    notifications_enabled: bool = True

    @model_validator(mode="after")
    def thresholds(self):
        if self.recover_value >= self.trigger_value:
            raise ValueError("恢复阈值必须低于触发阈值")
        return self


class SampleInput(BaseModel):
    station_id: int
    rule_id: int
    target: str = Field(min_length=1, max_length=100)
    value: Decimal = Field(max_digits=18, decimal_places=6)
    observed_at: datetime


class AcknowledgeInput(BaseModel):
    version: int = Field(ge=1)
    note: str = Field(min_length=1, max_length=2000)


def authorized(user):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "无权访问告警")


@router.get("/rules")
def rules(user: Actor, db: DB):
    authorized(user)
    return {
        "items": [
            {
                "id": r.id,
                "code": r.code,
                "version": r.version,
                "name": r.name,
                "metric": r.metric,
                "unit": r.unit,
                "trigger_value": str(r.trigger_value),
                "recover_value": str(r.recover_value),
                "notifications_enabled": r.notifications_enabled,
                "is_demo": r.is_demo,
            }
            for r in db.scalars(select(AlertRule).order_by(AlertRule.id.desc()))
        ]
    }


@router.post("/rules", status_code=201)
def create_rule(body: RuleInput, request: Request, user: Actor, db: DB):
    authorize_business_write(db, user, request, "alerts.rules")
    alerts.demo_only()
    if user.role != "regulator":
        raise HTTPException(403, "仅监管人员可配置规则")
    r = AlertRule(**body.model_dump(), is_demo=True)
    db.add(r)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, "规则代码与版本已存在") from e
    return {"id": r.id}


@router.post("/samples")
def sample(body: SampleInput, request: Request, user: Actor, db: DB):
    authorize_business_write(db, user, request, "alerts.samples")
    result = alerts.observe(db, user, **body.model_dump())
    db.commit()
    return result


@router.get("", response_model=AlertQueuePage)
def events(
    user: Actor,
    db: DB,
    q: str = Query("", max_length=150),
    status: str = Query("", max_length=30),
    acknowledged: bool | None = None,
    rule_id: int | None = Query(None, ge=1, le=9223372036854775807),
    record_id: int | None = Query(None, ge=1, le=9223372036854775807),
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    authorized(user)
    if status not in {"", "active", "recovered"}:
        raise HTTPException(422, "不支持的设备事件状态")
    for at in [start_at, end_at]:
        if at is not None and at.utcoffset() is None:
            raise HTTPException(422, "告警时间必须包含时区")
    if start_at and end_at and start_at >= end_at:
        raise HTTPException(422, "结束时间须晚于开始时间")
    stmt = scoped_records(
        select(AlertEvent, Station.name, Operator.name)
        .join(Station, Station.id == AlertEvent.station_id)
        .join(Operator, Operator.id == AlertEvent.operator_id),
        AlertEvent,
        user,
    )
    if user.role == "operator":
        stmt = stmt.where(AlertEvent.operator_id == user.operator_id)
    if q.strip():
        stmt = stmt.where(
            or_(
                AlertEvent.target.icontains(q.strip(), autoescape=True),
                Station.name.icontains(q.strip(), autoescape=True),
                Operator.name.icontains(q.strip(), autoescape=True),
            )
        )
    if status:
        stmt = stmt.where(AlertEvent.status == status)
    if acknowledged is not None:
        stmt = stmt.where(
            AlertEvent.acknowledged_at.is_not(None)
            if acknowledged
            else AlertEvent.acknowledged_at.is_(None)
        )
    if rule_id is not None:
        stmt = stmt.where(AlertEvent.rule_id == rule_id)
    if record_id is not None:
        stmt = stmt.where(AlertEvent.id == record_id)
    if start_at:
        stmt = stmt.where(AlertEvent.opened_at >= start_at)
    if end_at:
        stmt = stmt.where(AlertEvent.opened_at < end_at)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(
        stmt.order_by(AlertEvent.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return {
        "items": [
            {
                **alerts.output(e),
                "operator_id": e.operator_id,
                "rule_id": e.rule_id,
                "station_name": station_name,
                "operator_name": operator_name,
            }
            for e, station_name, operator_name in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{event_id}")
def detail(event_id: int, user: Actor, db: DB):
    e = alerts.access(db, user, event_id)
    return {
        **alerts.output(e),
        "notices": [
            {
                "phase": n.phase,
                "message": n.message,
                "created_at": n.created_at.isoformat(),
                "channel": "in_app",
            }
            for n in db.scalars(
                select(AlertNotice).where(AlertNotice.event_id == e.id).order_by(AlertNotice.id)
            )
        ],
    }


@router.post("/{event_id}/acknowledge")
def acknowledge(event_id: int, body: AcknowledgeInput, request: Request, user: Actor, db: DB):
    authorize_business_write(db, user, request, "alerts.acknowledge")
    alerts.demo_only()
    if user.role != "regulator":
        raise HTTPException(403, "仅监管人员可确认告警")
    e = alerts.access(db, user, event_id, True)
    if e.version != body.version or e.acknowledged_at:
        raise HTTPException(409, "记录已更新或已确认，请刷新")
    if not body.note.strip():
        raise HTTPException(422, "请填写确认依据")
    before = state(e)
    e.acknowledged_at = now()
    e.acknowledged_by = user.id
    e.acknowledge_note = body.note.strip()
    e.version += 1
    transition(db, user, "alerts.acknowledge", e.id, before, state(e), "alert_event")
    db.commit()
    return alerts.output(e)
