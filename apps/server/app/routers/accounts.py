from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.account_contracts import (
    ManagedAccountList,
    ManagedAccountResponse,
    RoleCatalogResponse,
    RoleOptionsResponse,
    RoleProfileResponse,
)
from app.authorization import BASE, LABELS, normalize_grants, permissions, require
from app.config import settings
from app.dependencies import DB, Actor, SnapshotActor, passwords
from app.models import Audit, Operator, RoleProfile, User
from app.reporting import SnapshotDB
from app.scopes import accounts_scope, company_scope, districts, require_global, validate_delegation
from app.sessions import revoke_users

router = APIRouter(tags=["authorization"])


class ProfileInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_role: str
    permissions: list[str] = Field(max_length=100)

    @model_validator(mode="after")
    def expand_legacy_grants(self):
        self.permissions = normalize_grants(self.base_role, self.permissions)
        return self


class ProfileEdit(ProfileInput):
    version: int = Field(ge=1)


class DistrictInput(BaseModel):
    allowed_districts: list[str] | None = Field(default=None, max_length=50)

    @field_validator("allowed_districts")
    @classmethod
    def normalize_districts(cls, value):
        if value is None:
            return None
        if any(not name.strip() or len(name.strip()) > 50 for name in value):
            raise ValueError("区域名称须为1至50字符")
        return sorted({name.strip() for name in value})


class AccountEdit(DistrictInput):
    version: int = Field(ge=1)
    role_profile_id: int
    enabled: bool
    reason: str = Field(min_length=1, max_length=1000)


class AccountInput(DistrictInput):
    username: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(
        min_length=12,
        max_length=200,
        repr=False,
        description="管理员设置的临时密码；新账号首次登录必须修改",
    )

    @field_validator("password")
    @classmethod
    def nonblank_password(cls, value):
        if not value.strip():
            raise ValueError("临时密码不能全部为空白")
        return value

    role_profile_id: int
    operator_id: int | None = None


def admin(user):
    if user.role != "regulator":
        raise HTTPException(403, "仅监管管理员可配置账号与权限")


def mutation_admin(user, db, capability):
    # Serialize grant/revocation writes, then re-read the issuer before delegating.
    db.execute(text("SELECT pg_advisory_xact_lock(78230012)"))
    db.expire_all()
    db.refresh(user)
    if not user.enabled:
        raise HTTPException(401, "账号已停用")
    admin(user)
    require(db, user, capability)
    if capability == "roles.manage":
        require_global(user)


def demo_only():
    if not settings.demo_mode:
        raise HTTPException(409, "授权配置暂限演示环境")


def profile_output(p):
    return {
        "id": p.id,
        "name": p.name,
        "base_role": p.base_role,
        "permissions": p.permissions,
        "version": p.version,
    }


def validate_grant(db, user, role, grants):
    if role not in BASE or not set(grants).issubset(BASE[role]):
        raise HTTPException(422, "权限超出基础角色边界")
    if "stations.read" not in grants:
        raise HTTPException(422, "当前工作台要求保留场站查看基础权限")
    for cap in grants:
        if (
            cap.startswith(
                (
                    "subsidy.",
                    "applications.",
                    "ratings.",
                    "inspections.",
                    "hazards.",
                    "alerts.",
                    "content.",
                )
            )
            and not cap.endswith(".read")
            or cap.endswith((".write", ".reply", ".status", ".export", ".review"))
        ):
            read = cap.split(".")[0] + ".read"
            if read not in grants:
                raise HTTPException(422, "办理或导出权限需要同时具备查看权限")
    if (
        set(grants).intersection(
            {
                "content.create",
                "content.edit",
                "content.publish",
                "content.withdraw",
                "content.delete",
                "content.export",
            }
        )
        and "content.manage" not in grants
    ):
        raise HTTPException(422, "内容办理权限需要同时具备草稿与历史查看权限")
    if not set(grants).issubset(permissions(db, user)):
        raise HTTPException(403, "不能下发自身未持有的权限")


def audit(db, user, action, oid, before, after):
    db.add(
        Audit(
            user_id=user.id, action=action, object_id=oid, detail={"before": before, "after": after}
        )
    )


@router.get("/api/v1/roles", response_model=RoleCatalogResponse)
def roles(user: SnapshotActor, db: SnapshotDB):
    admin(user)
    own = permissions(db, user)
    return {
        "catalog": [{"code": k, "name": v} for k, v in LABELS.items() if k in own],
        "base_roles": {k: sorted(v.intersection(own)) for k, v in BASE.items()},
        "items": [
            profile_output(p)
            for p in db.scalars(select(RoleProfile).order_by(RoleProfile.id))
            if set(p.permissions).issubset(own)
        ],
    }


@router.post("/api/v1/roles", status_code=201, response_model=RoleProfileResponse)
def create_profile(body: ProfileInput, user: Actor, db: DB):
    demo_only()
    mutation_admin(user, db, "roles.manage")
    validate_grant(db, user, body.base_role, body.permissions)
    if not body.name.strip():
        raise HTTPException(422, "请填写角色名称")
    p = RoleProfile(
        name=body.name.strip(), base_role=body.base_role, permissions=sorted(set(body.permissions))
    )
    db.add(p)
    try:
        db.flush()
        audit(db, user, "role.create", p.id, None, profile_output(p))
        result = RoleProfileResponse.model_validate(profile_output(p))
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, "角色名称已存在") from e
    return result


@router.patch("/api/v1/roles/{profile_id}", response_model=RoleProfileResponse)
def edit_profile(profile_id: int, body: ProfileEdit, user: Actor, db: DB):
    demo_only()
    mutation_admin(user, db, "roles.manage")
    p = db.scalar(select(RoleProfile).where(RoleProfile.id == profile_id).with_for_update())
    if not p:
        raise HTTPException(404, "角色不存在")
    if p.id == user.role_profile_id:
        raise HTTPException(403, "不能修改自身当前使用的角色")
    if not set(p.permissions).issubset(permissions(db, user)):
        raise HTTPException(403, "不能维护超出自身权限的角色")
    if p.version != body.version:
        raise HTTPException(409, "角色已更新，请刷新")
    if p.base_role != body.base_role:
        raise HTTPException(422, "已建立角色不能改变基础身份")
    if not body.name.strip():
        raise HTTPException(422, "请填写角色名称")
    validate_grant(db, user, body.base_role, body.permissions)
    before = profile_output(p)
    p.name = body.name.strip()
    p.permissions = sorted(set(body.permissions))
    p.version += 1
    revoke_users(db, select(User.id).where(User.role_profile_id == p.id))
    audit(db, user, "role.update", p.id, before, profile_output(p))
    result = RoleProfileResponse.model_validate(profile_output(p))
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, "角色名称已存在") from e
    return result


def account_output(db, u):
    return {
        "id": u.id,
        "username": u.username,
        "role": u.role,
        "operator_id": u.operator_id,
        "enabled": u.enabled,
        "must_change_password": u.must_change_password,
        "role_profile_id": u.role_profile_id,
        "version": u.version,
        "allowed_districts": u.allowed_districts,
        "permissions": sorted(permissions(db, u)),
    }


@router.get("/api/v1/accounts", response_model=ManagedAccountList)
def accounts(
    user: SnapshotActor,
    db: SnapshotDB,
    page: int = Query(1, ge=1),
    q: str = Query("", max_length=100),
):
    admin(user)
    stmt = accounts_scope(select(User), user)
    if q:
        stmt = stmt.where(User.username.ilike("%" + q + "%"))
    return {
        "items": [
            account_output(db, u)
            for u in db.scalars(stmt.order_by(User.id).offset((page - 1) * 20).limit(20))
        ],
        "total": db.scalar(select(func.count()).select_from(stmt.subquery())),
    }


@router.get("/api/v1/accounts/options", response_model=RoleOptionsResponse)
def account_options(user: SnapshotActor, db: SnapshotDB):
    admin(user)
    own = permissions(db, user)
    return {
        "items": [
            profile_output(p)
            for p in db.scalars(select(RoleProfile).order_by(RoleProfile.id))
            if set(p.permissions).issubset(own)
        ]
    }


@router.post("/api/v1/accounts", status_code=201, response_model=ManagedAccountResponse)
def create_account(body: AccountInput, user: Actor, db: DB):
    demo_only()
    mutation_admin(user, db, "accounts.manage")
    p = db.scalar(
        select(RoleProfile).where(RoleProfile.id == body.role_profile_id).with_for_update()
    )
    if not p:
        raise HTTPException(422, "请选择可配置角色")
    validate_grant(db, user, p.base_role, p.permissions)
    validate_delegation(user, body.allowed_districts)
    if p.base_role == "operator":
        op = (
            db.scalar(company_scope(select(Operator), user).where(Operator.id == body.operator_id))
            if body.operator_id
            else None
        )
        if not op or not op.enabled:
            raise HTTPException(422, "运营商账号需关联启用企业")
    elif body.operator_id is not None:
        raise HTTPException(422, "非运营商账号不能关联企业")
    u = User(
        username=body.username,
        password_hash=passwords.hash(body.password),
        must_change_password=True,
        role=p.base_role,
        operator_id=body.operator_id,
        role_profile_id=p.id,
        allowed_districts=body.allowed_districts,
    )
    db.add(u)
    try:
        db.flush()
        audit(db, user, "account.create", u.id, None, account_output(db, u))
        result = ManagedAccountResponse.model_validate(account_output(db, u))
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, "账号已存在") from e
    return result


@router.patch("/api/v1/accounts/{user_id}", response_model=ManagedAccountResponse)
def edit_account(user_id: int, body: AccountEdit, user: Actor, db: DB):
    demo_only()
    mutation_admin(user, db, "accounts.manage")
    if user_id == user.id:
        raise HTTPException(403, "不能变更自身角色或启停自身账号")
    u = db.scalar(accounts_scope(select(User), user).where(User.id == user_id).with_for_update())
    if not u:
        raise HTTPException(404, "账号不存在")
    if u.version != body.version:
        raise HTTPException(409, "账号版本已更新")
    if not permissions(db, u).issubset(permissions(db, user)):
        raise HTTPException(403, "不能维护权限高于自身的账号")
    p = db.scalar(
        select(RoleProfile).where(RoleProfile.id == body.role_profile_id).with_for_update()
    )
    if not p or p.base_role != u.role:
        raise HTTPException(422, "授权角色必须匹配账号基础身份")
    validate_grant(db, user, p.base_role, p.permissions)
    if not body.reason.strip():
        raise HTTPException(422, "请填写变更依据")
    target_scope = (
        body.allowed_districts if "allowed_districts" in body.model_fields_set else districts(u)
    )
    validate_delegation(user, target_scope)
    before = account_output(db, u)
    u.allowed_districts = target_scope
    u.role_profile_id = p.id
    u.enabled = body.enabled
    u.version += 1
    revoke_users(db, [u.id])
    audit(
        db,
        user,
        "account.update",
        u.id,
        before,
        {**account_output(db, u), "reason": body.reason.strip()},
    )
    result = ManagedAccountResponse.model_validate(account_output(db, u))
    db.commit()
    return result
