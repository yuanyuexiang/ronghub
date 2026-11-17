"""Global account administrators explicitly review and provision human staff accounts."""

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError

from app import staff_applications as staff
from app.contracts import StaffApplicationDetail, StaffApplicationPage, StaffRoleOptions
from app.dependencies import DB, Actor, SnapshotActor, passwords
from app.models import Audit, RoleProfile, StaffApplication, StaffApplicationAction, User, now
from app.password_management import session_active
from app.reporting import SnapshotDB
from app.routers.accounts import (
    DistrictInput,
    admin,
    mutation_admin,
    permissions,
    profile_output,
    validate_grant,
)
from app.scopes import require_global, validate_delegation

router = APIRouter(prefix="/api/v1/staff-applications", tags=["staff-applications"])


class ProvisionInput(DistrictInput):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    temporary_password: str = Field(min_length=12, max_length=200, repr=False)
    role_profile_id: Annotated[int, Field(strict=True, gt=0)]
    role_profile_version: Annotated[int, Field(strict=True, ge=1)]
    allowed_districts: list[str] | None

    @model_validator(mode="after")
    def password(self):
        if not self.temporary_password.strip():
            raise ValueError("初始密码不能全部为空白")
        return self


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["approve", "reject"]
    version: Annotated[int, Field(strict=True, ge=1)]
    comment: str = Field(min_length=1, max_length=1000)
    account: ProvisionInput | None = None

    @model_validator(mode="after")
    def validate_action(self):
        if not self.comment.strip():
            raise ValueError("请填写反馈给申请人的审核意见")
        if (self.action == "approve") != (self.account is not None):
            raise ValueError("通过必须明确分配账号、角色与区域，驳回不能携带账号凭据")
        if self.account and self.account.temporary_password in self.comment:
            raise ValueError("审核意见不得包含临时密码")
        return self


def guard(user):
    admin(user)
    require_global(user)


def detail_output(db, row):
    return {
        **staff.output(row, True),
        "history": [
            {
                "actor_name": db.get(User, a.actor_id).username,
                "action": a.action,
                "snapshot": a.snapshot,
                "created_at": a.created_at.isoformat(),
            }
            for a in db.scalars(
                select(StaffApplicationAction)
                .where(StaffApplicationAction.application_id == row.id)
                .order_by(StaffApplicationAction.id)
            )
        ],
    }


@router.get("", response_model=StaffApplicationPage)
def listing(
    user: SnapshotActor,
    db: SnapshotDB,
    q: str = Query("", max_length=150),
    status: str = Query("", pattern="^(|pending|approved|rejected)$"),
    requested_role: str = Query("", pattern="^(|regulator|association)$"),
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    guard(user)
    stmt = select(StaffApplication)
    if q.strip():
        stmt = stmt.where(
            or_(
                StaffApplication.applicant_name.icontains(q.strip(), autoescape=True),
                StaffApplication.contact_phone.icontains(q.strip(), autoescape=True),
                StaffApplication.username.icontains(q.strip(), autoescape=True),
            )
        )
    if status:
        stmt = stmt.where(StaffApplication.status == status)
    if requested_role:
        stmt = stmt.where(StaffApplication.requested_role == requested_role)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    return {
        "items": [
            staff.output(row, True)
            for row in db.scalars(
                stmt.order_by(StaffApplication.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/role-options", response_model=StaffRoleOptions)
def role_options(
    user: SnapshotActor,
    db: SnapshotDB,
    base_role: Literal["regulator", "association"],
    q: str = Query("", max_length=100),
    page: int = Query(1, ge=1, le=2147483647),
):
    guard(user)
    stmt = select(RoleProfile).where(
        RoleProfile.base_role == base_role,
        cast(RoleProfile.permissions, JSONB).contained_by(sorted(permissions(db, user))),
    )
    if q.strip():
        stmt = stmt.where(RoleProfile.name.icontains(q.strip(), autoescape=True))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    return {
        "items": [
            profile_output(row)
            for row in db.scalars(stmt.order_by(RoleProfile.id).offset((page - 1) * 20).limit(20))
        ],
        "total": total,
        "page": page,
        "page_size": 20,
    }


@router.get("/{application_id}", response_model=StaffApplicationDetail)
def detail(application_id: int, user: Actor, db: DB):
    guard(user)
    row = db.get(StaffApplication, application_id)
    if not row:
        raise HTTPException(404, "申请不存在")
    return detail_output(db, row)


@router.post("/{application_id}/review", response_model=StaffApplicationDetail)
def review(application_id: int, body: ReviewInput, request: Request, user: Actor, db: DB):
    staff.demo_only()
    mutation_admin(user, db, "accounts.manage")
    guard(user)
    session_active(db, request, user)
    row = db.scalar(
        select(StaffApplication).where(StaffApplication.id == application_id).with_for_update()
    )
    if not row:
        raise HTTPException(404, "申请不存在")
    if row.status != "pending" or row.version != body.version:
        raise HTTPException(409, "申请状态或版本已变化，请刷新")
    before = {"status": row.status, "version": row.version}
    if body.action == "approve":
        account = body.account
        profile = db.scalar(
            select(RoleProfile).where(RoleProfile.id == account.role_profile_id).with_for_update()
        )
        if not profile or profile.base_role != row.requested_role:
            raise HTTPException(422, "请选择与申请身份一致的角色")
        if profile.version != account.role_profile_version:
            raise HTTPException(409, "角色配置已变化，请重新选择角色后审核")
        validate_grant(db, user, profile.base_role, profile.permissions)
        validate_delegation(user, account.allowed_districts)
        target = User(
            username=account.username,
            password_hash=passwords.hash(account.temporary_password),
            role=profile.base_role,
            role_profile_id=profile.id,
            allowed_districts=account.allowed_districts,
            must_change_password=True,
        )
        db.add(target)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "账号已存在，本次申请未开通") from None
        row.user_id = target.id
        row.username = target.username
        row.grant_snapshot = {
            "role_profile_id": profile.id,
            "profile_name": profile.name,
            "profile_version": profile.version,
            "base_role": profile.base_role,
            "permissions": list(profile.permissions),
            "allowed_districts": account.allowed_districts,
        }
        row.status = "approved"
    else:
        row.status = "rejected"
    row.version += 1
    row.reviewed_at = now()
    row.review_comment = body.comment.strip()
    snapshot = {
        "status": row.status,
        "version": row.version,
        "user_id": row.user_id,
        "username": row.username,
        "grant_snapshot": row.grant_snapshot,
        "review_comment": row.review_comment,
    }
    db.add(
        StaffApplicationAction(
            application_id=row.id, actor_id=user.id, action=body.action, snapshot=snapshot
        )
    )
    db.add(
        Audit(
            user_id=user.id,
            action="staff_application." + body.action,
            object_id=row.id,
            detail={
                "before": before,
                "after": {"status": row.status, "version": row.version, "user_id": row.user_id},
            },
        )
    )
    db.flush()
    result = StaffApplicationDetail.model_validate(detail_output(db, row))
    db.commit()
    return result
