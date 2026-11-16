from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app import subsidy
from app.authorization import SUBSIDY_ACTIONS, authorize_business_write
from app.business_contracts import (
    SubsidyBatchResult,
    SubsidyClaimDetail,
    SubsidyClaimResponse,
    SubsidyEnergyList,
    SubsidyPolicyList,
    SubsidyPolicyResponse,
)
from app.business_dates import Dates, apply_dates, submission_time
from app.contracts import SubsidyQueuePage
from app.dependencies import DB, Actor
from app.models import (
    Audit,
    EnergyRecord,
    Operator,
    Station,
    SubsidyAction,
    SubsidyClaim,
    SubsidyDeviceLine,
    SubsidyPolicy,
)
from app.reporting import SnapshotDB
from app.scopes import records as scoped_records
from app.subsidy_execution_contracts import DeviceLinePage, DeviceLineResponse
from app.subsidy_rules import DeviceExecutableRules, DeviceSubsidyRules

router = APIRouter(prefix="/api/v1/subsidy", tags=["subsidy"])


class PolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_rules: DeviceSubsidyRules | DeviceExecutableRules | None = None
    code: str = Field(min_length=1, max_length=60)
    version: int = Field(ge=1, le=2147483647, strict=True)
    name: str = Field(min_length=1, max_length=150)
    rate: Decimal = Field(gt=0, max_digits=12, decimal_places=6)
    effective_from: date
    effective_to: date


class ClaimInput(BaseModel):
    station_id: int
    policy_id: int
    start_date: date
    end_date: date
    application_note: str = Field(default="", max_length=2000)


class ActionInput(BaseModel):
    action: str
    version: int
    comment: str = Field(default="", max_length=2000)
    delta: Decimal = Field(default=Decimal(0), max_digits=18, decimal_places=2)


class SubsidyBatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Annotated[int, Field(strict=True, gt=0, le=2147483647)]
    version: Annotated[int, Field(strict=True, ge=1, le=2147483647)]


class SubsidyBatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["approve", "reject"]
    items: list[SubsidyBatchItem] = Field(min_length=1, max_length=100)
    comment: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_batch(self):
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("批量补贴编号不能重复")
        if not self.comment.strip():
            raise ValueError("请填写统一审核意见")
        return self


def policy_output(p):
    return {
        "device_rules": p.device_rules,
        "calculation_basis": "legacy_station_energy"
        if p.device_rules is None
        else "demo_device_day_v2"
        if p.device_rules.get("configuration_version") == 2
        else "device_rules_pending",
        "claim_supported": p.device_rules is None
        or p.device_rules.get("configuration_version") == 2,
        "id": p.id,
        "code": p.code,
        "version": p.version,
        "name": p.name,
        "rate": str(p.rate),
        "effective_from": p.effective_from.isoformat(),
        "effective_to": p.effective_to.isoformat(),
        "enabled": p.enabled,
        "is_demo": p.is_demo,
    }


@router.get("/policies", response_model=SubsidyPolicyList)
def policies(user: Actor, db: DB):
    if user.role not in {"operator", "regulator"}:
        raise HTTPException(403, "无权访问补贴政策")
    return {
        "items": [
            policy_output(p)
            for p in db.scalars(select(SubsidyPolicy).order_by(SubsidyPolicy.id.desc()))
        ]
    }


@router.post("/policies", status_code=201, response_model=SubsidyPolicyResponse)
def create_policy(body: PolicyInput, user: Actor, db: DB, request: Request):
    authorize_business_write(db, user, request, "subsidy.policies")
    subsidy.demo_only()
    if user.role != "regulator":
        raise HTTPException(403, "仅监管人员可创建政策版本")
    if body.effective_from > body.effective_to:
        raise HTTPException(422, "有效期不正确")
    if not body.code.strip() or not body.name.strip() or "\x00" in body.code + body.name:
        raise HTTPException(422, "政策名称与代码不能为空或包含无效字符")
    p = SubsidyPolicy(
        **body.model_dump(exclude={"device_rules"}),
        device_rules=body.device_rules.model_dump(mode="json") if body.device_rules else None,
        is_demo=True,
    )
    db.add(p)
    try:
        db.flush()
        result = SubsidyPolicyResponse.model_validate(policy_output(p))
        db.add(
            Audit(
                user_id=user.id,
                action="subsidy.policy.create",
                object_id=p.id,
                detail={
                    "code": p.code,
                    "version": p.version,
                    "calculation_basis": result.calculation_basis,
                },
            )
        )
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, "政策代码及版本已存在") from e
    return result


@router.get("/energy", response_model=SubsidyEnergyList)
def energy(user: Actor, db: DB, station_id: int):
    from app.dependencies import scope

    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "无权查看电量")
    if not db.scalar(scope(select(Station).where(Station.id == station_id), user)):
        raise HTTPException(404, "场站不存在或不可访问")
    return {
        "items": [
            {
                "id": e.id,
                "day": e.day.isoformat(),
                "kwh": str(e.kwh),
                "source": e.source,
                "eligible": e.eligible,
            }
            for e in db.scalars(
                select(EnergyRecord)
                .where(EnergyRecord.station_id == station_id)
                .order_by(EnergyRecord.day.desc())
                .limit(100)
            )
        ]
    }


@router.post("/claims", status_code=201, response_model=SubsidyClaimResponse)
def create_claim(body: ClaimInput, user: Actor, db: DB, request: Request):
    authorize_business_write(db, user, request, "subsidy.create")
    c = subsidy.create_claim(
        db,
        user,
        body.station_id,
        body.policy_id,
        body.start_date,
        body.end_date,
        body.application_note,
    )
    result = SubsidyClaimResponse.model_validate(subsidy.output(c))
    db.commit()
    return result


def claims_statement(user, q="", status="", record_id=None, dates=None):
    if status not in {"", "draft", "submitted", "approved", "rejected", "withdrawn", "disbursed"}:
        raise HTTPException(422, "不支持的办理状态")
    stmt = scoped_records(
        select(
            SubsidyClaim,
            Station.name,
            Operator.name,
            Station.status,
            submission_time(SubsidyClaim).label("last_submitted_at"),
        )
        .join(Station, Station.id == SubsidyClaim.station_id)
        .join(Operator, Operator.id == SubsidyClaim.operator_id),
        SubsidyClaim,
        user,
    )
    if user.role == "operator" and user.operator_id is not None:
        stmt = stmt.where(SubsidyClaim.operator_id == user.operator_id)
    elif user.role not in {"regulator"}:
        raise HTTPException(403, "无权查看申报记录")
    if q.strip():
        stmt = stmt.where(
            or_(
                Station.name.icontains(q.strip(), autoescape=True),
                Operator.name.icontains(q.strip(), autoescape=True),
            )
        )
    if status:
        stmt = stmt.where(SubsidyClaim.status == status)
    if record_id is not None:
        stmt = stmt.where(SubsidyClaim.id == record_id)
    return apply_dates(stmt, SubsidyClaim, dates)


@router.get("/claims", response_model=SubsidyQueuePage)
def claims(
    user: Actor,
    db: SnapshotDB,
    dates: Dates,
    q: str = Query("", max_length=150),
    status: str = Query("", max_length=30),
    record_id: int | None = Query(None, ge=1, le=9223372036854775807),
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    stmt = claims_statement(user, q, status, record_id, dates)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(
        stmt.order_by(SubsidyClaim.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return {
        "items": [
            {
                **subsidy.output(c),
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


@router.post("/claims/batch-actions", response_model=SubsidyBatchResult)
def batch_actions(body: SubsidyBatchInput, user: Actor, db: DB, request: Request):
    authorize_business_write(db, user, request, "subsidy.review")
    subsidy.demo_only()
    try:
        # All batches acquire claim locks in ID order, independent of UI/input order.
        records = [
            subsidy.act(db, user, item.id, body.action, item.version, body.comment)
            for item in sorted(body.items, key=lambda item: item.id)
        ]
        db.add(
            Audit(
                user_id=user.id,
                action="subsidy.batch_review",
                object_id=None,
                detail={
                    "decision": body.action,
                    "count": len(records),
                    "claims": [
                        {
                            "id": record.id,
                            "before_version": record.version - 1,
                            "after_version": record.version,
                        }
                        for record in records
                    ],
                },
            )
        )
        result = SubsidyBatchResult.model_validate(
            {
                "items": [subsidy.output(record) for record in records],
                "total": len(records),
            }
        )
        db.commit()
    except HTTPException as error:
        db.rollback()
        raise HTTPException(error.status_code, f"{error.detail}，整批未提交") from error
    except Exception:
        db.rollback()
        raise
    return result


@router.get("/claims/{claim_id}", response_model=SubsidyClaimDetail)
def detail(claim_id: int, user: Actor, db: DB):
    c = subsidy.claim_access(db, user, claim_id)
    return {
        **subsidy.output(c),
        "snapshot": c.snapshot,
        "history": [
            {
                "action": a.action,
                "comment": a.comment,
                "delta": str(a.delta),
                "created_at": a.created_at.isoformat(),
            }
            for a in db.scalars(
                select(SubsidyAction)
                .where(SubsidyAction.claim_id == claim_id)
                .order_by(SubsidyAction.id)
            )
        ],
    }


@router.get("/claims/{claim_id}/device-lines", response_model=DeviceLinePage)
def device_lines(
    claim_id: int,
    user: Actor,
    db: SnapshotDB,
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    claim = subsidy.claim_access(db, user, claim_id)
    if (claim.snapshot or {}).get("device_calculation") is None:
        raise HTTPException(409, "该申请未留存设备核算依据；未将旧站级明细补成设备数据")
    stmt = select(SubsidyDeviceLine).where(SubsidyDeviceLine.claim_id == claim_id)
    rows = db.scalars(
        stmt.order_by(SubsidyDeviceLine.fact_id).offset((page - 1) * page_size).limit(page_size)
    )
    return {
        "items": [
            DeviceLineResponse(claim_id=row.claim_id, fact_id=row.fact_id, snapshot=row.snapshot)
            for row in rows
        ],
        "total": db.scalar(select(func.count()).select_from(stmt.subquery())),
        "page": page,
        "page_size": page_size,
    }


@router.post("/claims/{claim_id}/actions", response_model=SubsidyClaimResponse)
def action(claim_id: int, body: ActionInput, user: Actor, db: DB, request: Request):
    capability = SUBSIDY_ACTIONS.get(body.action)
    if capability is None:
        raise HTTPException(422, "不支持的补贴动作")
    authorize_business_write(db, user, request, capability)
    c = subsidy.act(db, user, claim_id, body.action, body.version, body.comment, body.delta)
    result = SubsidyClaimResponse.model_validate(subsidy.output(c))
    db.commit()
    return result
