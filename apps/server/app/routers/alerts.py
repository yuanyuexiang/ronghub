from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import alerts
from app.alert_queries import Category
from app.alert_queries import query as event_query
from app.authorization import authorize_business_write
from app.business_audit import state, transition
from app.configured_alert_contracts import ObservationInput
from app.contracts import AlertDetail, AlertEventResponse, AlertQueuePage
from app.dependencies import DB, Actor, SnapshotActor
from app.models import AlertEvent, AlertNotice, AlertRule, now
from app.operational_contracts import LegacyAlertRules, LegacyRuleCreated, LegacySampleResult
from app.reporting import SnapshotDB

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

    @field_validator("observed_at")
    @classmethod
    def valid_time(cls, value):
        return ObservationInput.aware(value)


class AcknowledgeInput(BaseModel):
    version: int = Field(ge=1)
    note: str = Field(min_length=1, max_length=2000)


def authorized(user):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "无权访问告警")


@router.get("/rules", response_model=LegacyAlertRules)
def rules(user: SnapshotActor, db: SnapshotDB, response: Response):
    response.headers["Cache-Control"] = "no-store"
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
            for r in db.scalars(
                select(AlertRule)
                .where(AlertRule.configuration.is_(None))
                .order_by(AlertRule.id.desc())
            )
        ]
    }


@router.post("/rules", status_code=201, response_model=LegacyRuleCreated)
def create_rule(body: RuleInput, request: Request, user: Actor, db: DB, response: Response):
    authorize_business_write(db, user, request, "alerts.rules")
    alerts.demo_only()
    if user.role != "regulator":
        raise HTTPException(403, "仅监管人员可配置规则")
    r = AlertRule(**body.model_dump(), is_demo=True)
    db.add(r)
    try:
        db.flush()
        result = LegacyRuleCreated.model_validate({"id": r.id})
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, "规则代码与版本已存在") from e
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/samples", response_model=LegacySampleResult)
def sample(body: SampleInput, request: Request, user: Actor, db: DB, response: Response):
    authorize_business_write(db, user, request, "alerts.samples")
    result = LegacySampleResult.model_validate(alerts.observe(db, user, **body.model_dump()))
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("", response_model=AlertQueuePage)
def events(
    user: SnapshotActor,
    db: SnapshotDB,
    response: Response,
    q: str = Query("", max_length=150),
    status: str = Query("", max_length=30),
    acknowledged: bool | None = None,
    category: Category | None = None,
    rule_id: int | None = Query(None, ge=1, le=9223372036854775807),
    record_id: int | None = Query(None, ge=1, le=9223372036854775807),
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    authorized(user)
    response.headers["Cache-Control"] = "no-store"
    stmt = event_query(
        user,
        q=q,
        status=status,
        acknowledged=acknowledged,
        rule_id=rule_id,
        record_id=record_id,
        start_at=start_at,
        end_at=end_at,
        category=category,
    )
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


@router.get("/{event_id}", response_model=AlertDetail)
def detail(event_id: int, user: SnapshotActor, db: SnapshotDB, response: Response):
    response.headers["Cache-Control"] = "no-store"
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


@router.post("/{event_id}/acknowledge", response_model=AlertEventResponse)
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
    result = AlertEventResponse.model_validate(alerts.output(e))
    db.commit()
    return result
