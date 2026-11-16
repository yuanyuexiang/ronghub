from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from app import password_management as management
from app.dependencies import DB, Actor, passwords
from app.models import User
from app.routers.accounts import accounts_scope, mutation_admin, permissions

router = APIRouter(tags=["passwords"])


class ChangeInput(BaseModel):
    current_password: str = Field(min_length=1, max_length=200, repr=False)
    new_password: str = Field(min_length=12, max_length=200, repr=False)


class ResetInput(BaseModel):
    admin_password: str = Field(min_length=1, max_length=200, repr=False)
    temporary_password: str = Field(min_length=12, max_length=200, repr=False)
    version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)


@router.post("/api/v1/auth/password")
def change_password(body: ChangeInput, request: Request, response: Response, user: Actor, db: DB):
    key = management.attempt(db, user.id, "change")
    db.execute(text("SELECT pg_advisory_xact_lock(78230012)"))
    fresh = db.scalar(
        select(User)
        .where(User.id == user.id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    if not fresh:
        raise HTTPException(401, "账号已失效")
    management.session_active(db, request, fresh)
    if not passwords.verify(body.current_password, fresh.password_hash):
        raise HTTPException(403, "当前密码不正确")
    management.set_password(
        db, fresh, fresh, body.new_password, False, "password.change", "本人验证当前密码修改", key
    )
    db.commit()
    response.delete_cookie("ronghub_session", path="/")
    return {"ok": True, "reauthenticate": True}


@router.post("/api/v1/accounts/{user_id}/password-reset")
def reset_password(user_id: int, body: ResetInput, request: Request, user: Actor, db: DB):
    key = management.attempt(db, user.id, "reset")
    mutation_admin(user, db, "accounts.manage")
    management.session_active(db, request, user)
    if not passwords.verify(body.admin_password, user.password_hash):
        raise HTTPException(403, "管理员密码验证失败")
    if user_id == user.id:
        raise HTTPException(403, "请通过本人修改密码入口办理")
    target = db.scalar(
        accounts_scope(select(User), user)
        .where(User.id == user_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    if not target:
        raise HTTPException(404, "账号不存在或不可管理")
    if not permissions(db, target).issubset(permissions(db, user)):
        raise HTTPException(403, "不能重置权限高于自身的账号")
    if target.version != body.version:
        raise HTTPException(409, "账号已变化，请刷新后重试")
    if not body.reason.strip():
        raise HTTPException(422, "请填写已核验身份的重置依据")
    management.set_password(
        db, user, target, body.temporary_password, True, "password.reset", body.reason.strip(), key
    )
    db.commit()
    return {
        "ok": True,
        "user_id": target.id,
        "version": target.version,
        "must_change_password": True,
    }
