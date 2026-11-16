from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import enrollment
from app.authorization import permissions, require
from app.config import settings
from app.dependencies import DB, Actor
from app.models import (
    Audit,
    Operator,
    Registration,
    RegistrationAction,
    RegistrationAttachment,
    RoleProfile,
    User,
)
from app.routers.accounts import mutation_admin, validate_grant
from app.routers.attachments import metadata

router = APIRouter(prefix="/api/v1/registrations", tags=["registrations"])


class ReviewInput(BaseModel):
    version: int = Field(ge=1)
    action: str
    comment: str = Field(min_length=1, max_length=2000)
    role_profile_id: int | None = None
    role_profile_version: int | None = Field(default=None, ge=1)


def regulator(user):
    if user.role != "regulator":
        raise HTTPException(403, "仅监管人员可审核企业注册")


@router.get("")
def listing(user: Actor, db: DB, page: int = Query(1, ge=1), q: str = Query("", max_length=150)):
    regulator(user)
    stmt = select(Registration).where(Registration.status != "draft")
    if q:
        stmt = stmt.where(Registration.company_name.ilike("%" + q + "%"))
    return {
        "items": [
            enrollment.output(r)
            for r in db.scalars(
                stmt.order_by(Registration.id.desc()).offset((page - 1) * 20).limit(20)
            )
        ],
        "total": db.scalar(select(func.count()).select_from(stmt.subquery())),
    }


@router.get("/options")
def options(user: Actor, db: DB):
    regulator(user)
    require(db, user, "registrations.review")
    own = permissions(db, user)
    return {
        "items": [
            {"id": p.id, "name": p.name, "version": p.version}
            for p in db.scalars(
                select(RoleProfile)
                .where(RoleProfile.base_role == "operator")
                .order_by(RoleProfile.id)
            )
            if set(p.permissions).issubset(own)
        ]
    }


@router.get("/{registration_id}")
def detail(registration_id: int, user: Actor, db: DB):
    regulator(user)
    r = db.get(Registration, registration_id)
    if not r or r.status == "draft":
        raise HTTPException(404, "注册申请不存在")
    return {
        **enrollment.output(r),
        "history": [
            {"action": a.action, "comment": a.comment, "created_at": a.created_at.isoformat()}
            for a in db.scalars(
                select(RegistrationAction)
                .where(RegistrationAction.registration_id == r.id)
                .order_by(RegistrationAction.id)
            )
        ],
        "attachments": [
            metadata(a)
            for a in db.scalars(
                select(RegistrationAttachment).where(RegistrationAttachment.registration_id == r.id)
            )
        ],
    }


@router.post("/{registration_id}/review")
def review(registration_id: int, body: ReviewInput, user: Actor, db: DB):
    enrollment.demo_only()
    mutation_admin(user, db, "registrations.review")
    r = db.scalar(select(Registration).where(Registration.id == registration_id).with_for_update())
    if not r:
        raise HTTPException(404, "注册申请不存在")
    if r.version != body.version or r.status != "submitted":
        raise HTTPException(409, "申请已变化或不在待审核状态")
    if not body.comment.strip():
        raise HTTPException(422, "请填写审核依据")
    granted = None
    if body.action == "approve":
        p = (
            db.scalar(
                select(RoleProfile).where(RoleProfile.id == body.role_profile_id).with_for_update()
            )
            if body.role_profile_id
            else None
        )
        if not p or p.base_role != "operator":
            raise HTTPException(422, "审核通过需指定企业账号权限角色")
        if body.role_profile_version is None:
            raise HTTPException(422, "请明确所选权限角色版本")
        if body.role_profile_version != p.version:
            raise HTTPException(409, "所选权限角色已更新，请刷新后再审核")
        validate_grant(db, user, p.base_role, p.permissions)
        granted = {"profile_id": p.id, "version": p.version, "permissions": p.permissions}
        op = Operator(
            name=r.company_name,
            credit_code=r.credit_code,
            registration_address=r.registration_address,
            contacts=r.contacts,
        )
        db.add(op)
        try:
            db.flush()
            account = User(
                username=r.username,
                password_hash=r.password_hash,
                role="operator",
                operator_id=op.id,
                role_profile_id=p.id,
            )
            db.add(account)
            db.flush()
        except IntegrityError as e:
            db.rollback()
            raise HTTPException(409, "企业或账号与已有记录冲突，请核实后处理") from e
        r.operator_id = op.id
        r.user_id = account.id
        r.status = "approved"
    elif body.action == "reject":
        r.status = "rejected"
    else:
        raise HTTPException(422, "不支持的审核动作")
    r.version += 1
    enrollment.record(db, r, body.action, body.comment.strip(), user.id)
    db.add(
        Audit(
            user_id=user.id,
            action="registration." + body.action,
            object_id=r.id,
            detail={
                "version": r.version,
                "operator_id": r.operator_id,
                "user_id": r.user_id,
                "role_profile_id": body.role_profile_id,
                "grant_snapshot": granted,
            },
        )
    )
    db.commit()
    return enrollment.output(r)


@router.get("/{registration_id}/attachments/{material_id}/download")
def download(registration_id: int, material_id: int, user: Actor, db: DB):
    regulator(user)
    parent = db.get(Registration, registration_id)
    a = db.get(RegistrationAttachment, material_id)
    if not parent or parent.status == "draft" or not a or a.registration_id != registration_id:
        raise HTTPException(404, "材料不存在")
    path = Path(settings.storage_path).resolve() / a.storage_key
    if not path.is_file():
        raise HTTPException(404, "材料文件不可用")
    return FileResponse(
        path,
        filename=a.filename,
        media_type=a.content_type,
        headers={"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"},
    )
