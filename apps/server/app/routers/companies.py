"""Operator company profiles, private contacts and audited demonstration maintenance."""

import csv
import io

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.company_contracts import CompanyDetail, CompanyList, CompanyProfile, CompanySummary
from app.config import settings
from app.dependencies import DB, Actor, SnapshotActor
from app.models import Audit, Operator, User
from app.reporting import SnapshotDB
from app.routers.statistics import csv_cell
from app.scopes import company_scope
from app.sessions import revoke_users

EXPORT_LIMIT = 10000
CSV = {200: {"content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}}}}

router = APIRouter(prefix="/api/v1/companies", tags=["companies"])


class ContactInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    phone: str = Field(min_length=1, max_length=80)
    email: str = Field(default="", max_length=150)
    position: str = Field(default="", max_length=100)
    primary: bool = False

    @field_validator("name", "phone")
    @classmethod
    def nonblank(cls, v):
        if not v.strip():
            raise ValueError("联系人姓名与电话不能为空")
        return v.strip()


class ProfileInput(BaseModel):
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=150)
    credit_code: str | None = Field(default=None, pattern=r"^[0-9A-Z]{18}$")
    registration_address: str = Field(default="", max_length=255)
    legal_representative: str = Field(default="", max_length=100)
    service_phone: str = Field(default="", max_length=80)
    contacts: list[ContactInput] = Field(default_factory=list, max_length=10)

    @field_validator("credit_code", mode="before")
    @classmethod
    def normalize_code(cls, v):
        return str(v).strip().upper() or None if v is not None else None

    @field_validator("name")
    @classmethod
    def nonblank(cls, v):
        if not v.strip():
            raise ValueError("企业名称不能为空")
        return v.strip()

    @model_validator(mode="after")
    def primary_contact(self):
        if sum(c.primary for c in self.contacts) > 1:
            raise ValueError("最多设置一个主要联系人")
        return self


class StatusInput(BaseModel):
    version: int = Field(ge=1)
    enabled: bool
    reason: str = Field(min_length=1, max_length=1000)


def scoped(stmt, user):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "无权查看企业私有资料")
    return company_scope(stmt, user)


def access(db, user, oid, lock=False):
    stmt = scoped(select(Operator).where(Operator.id == oid), user)
    op = db.scalar(stmt.with_for_update() if lock else stmt)
    if not op:
        raise HTTPException(404, "企业不存在或不可访问")
    return op


def output(op, private=False):
    data = {
        "id": op.id,
        "name": op.name,
        "credit_code": op.credit_code,
        "enabled": op.enabled,
        "version": op.version,
    }
    if private:
        data.update(
            registration_address=op.registration_address,
            legal_representative=op.legal_representative,
            service_phone=op.service_phone,
            contacts=op.contacts,
        )
    return data


def demo_only():
    if not settings.demo_mode:
        raise HTTPException(409, "企业资料维护与启停规则暂限演示环境")


def filtered(user, q, credit_code):
    stmt = scoped(select(Operator), user)
    if q:
        stmt = stmt.where(Operator.name.icontains(q, autoescape=True))
    if credit_code:
        stmt = stmt.where(Operator.credit_code == credit_code.strip().upper())
    return stmt


@router.get("", response_model=CompanyList)
def listing(
    user: SnapshotActor,
    db: SnapshotDB,
    q: str = Query("", max_length=150),
    credit_code: str = Query("", max_length=18),
    page: int = Query(1, ge=1),
):
    stmt = filtered(user, q, credit_code)
    return {
        "items": [
            output(op)
            for op in db.scalars(stmt.order_by(Operator.id).offset((page - 1) * 20).limit(20))
        ],
        "total": db.scalar(select(func.count()).select_from(stmt.subquery())),
    }


@router.get("/export", response_class=Response, responses=CSV)
def export(
    user: SnapshotActor,
    db: SnapshotDB,
    q: str = Query("", max_length=150),
    credit_code: str = Query("", max_length=18),
):
    stmt = filtered(user, q, credit_code)
    rows = list(db.scalars(stmt.order_by(Operator.id).limit(EXPORT_LIMIT + 1)))
    if len(rows) > EXPORT_LIMIT:
        raise HTTPException(422, "单次最多导出10000条，请缩小查询范围；未截断导出")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["企业名称", "信用代码", "账号状态", "注册地址", "客服电话"])
    for op in rows:
        writer.writerow(
            [
                csv_cell(v)
                for v in [
                    op.name,
                    op.credit_code or "尚未补充",
                    "正常" if op.enabled else "停用",
                    op.registration_address,
                    op.service_phone,
                ]
            ]
        )
    db.add(
        Audit(
            user_id=user.id,
            action="company.export",
            detail={"rows": len(rows), "q": q, "credit_code": credit_code},
        )
    )
    db.commit()
    return Response(
        "\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="ronghub-companies.csv"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/{company_id}", response_model=CompanyDetail)
def detail(company_id: int, user: SnapshotActor, db: SnapshotDB):
    op = access(db, user, company_id)
    return {
        **output(op, True),
        "history": [
            {"action": a.action, "detail": a.detail, "created_at": a.created_at.isoformat()}
            for a in db.scalars(
                select(Audit)
                .where(
                    Audit.object_id == op.id, Audit.action.in_(["company.update", "company.status"])
                )
                .order_by(Audit.id.desc())
                .limit(100)
            )
        ],
    }


@router.patch("/{company_id}", response_model=CompanyProfile)
def edit(company_id: int, body: ProfileInput, user: Actor, db: DB):
    demo_only()
    op = access(db, user, company_id, True)
    if op.version != body.version:
        raise HTTPException(409, "企业资料已更新，请刷新后再维护")
    before = output(op, True)
    for key, value in body.model_dump(exclude={"version"}).items():
        setattr(op, key, value)
    op.version += 1
    db.add(
        Audit(
            user_id=user.id,
            action="company.update",
            object_id=op.id,
            detail={"before": before, "after": output(op, True)},
        )
    )
    result = CompanyProfile.model_validate(output(op, True))
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, "企业名称或信用代码已被使用") from e
    return result


@router.post("/{company_id}/status", response_model=CompanySummary)
def status(company_id: int, body: StatusInput, user: Actor, db: DB):
    demo_only()
    if user.role != "regulator":
        raise HTTPException(403, "仅监管人员可启停企业账号")
    op = access(db, user, company_id, True)
    if op.version != body.version:
        raise HTTPException(409, "企业版本已变化，请刷新")
    if not body.reason.strip():
        raise HTTPException(422, "请填写启停原因")
    if not body.enabled:
        revoke_users(db, select(User.id).where(User.operator_id == op.id))
    before = op.enabled
    op.enabled = body.enabled
    op.version += 1
    db.add(
        Audit(
            user_id=user.id,
            action="company.status",
            object_id=op.id,
            detail={"before": before, "after": op.enabled, "reason": body.reason.strip()},
        )
    )
    result = CompanySummary.model_validate(output(op))
    db.commit()
    return result
