import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import captcha
from app.authorization import permissions
from app.config import settings
from app.contracts import CurrentUser, LoginCaptcha, LoginResponse, OkResponse
from app.dependencies import DB, Actor, passwords, token_hash
from app.models import LoginSession, Operator, User

router = APIRouter()


class LoginInput(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=200)
    captcha_id: str = Field(min_length=32, max_length=128)
    captcha_answer: str = Field(min_length=1, max_length=12)


@router.get("/api/v1/auth/captcha", response_model=LoginCaptcha)
def challenge(request: Request, response: Response, db: DB):
    return captcha.issue(db, request, response)


@router.post("/api/v1/auth/login", response_model=LoginResponse)
def login(body: LoginInput, response: Response, request: Request, db: DB):
    from app.security import throttle

    captcha.verify(db, request, body.captcha_id, body.captcha_answer)
    throttle(db, body.username, request.client.host if request.client else "unknown")
    user = db.scalar(
        select(User).where(User.username == body.username).with_for_update(key_share=True)
    )
    from app.dependencies import DUMMY_PASSWORD_HASH

    valid = passwords.verify(body.password, user.password_hash if user else DUMMY_PASSWORD_HASH)
    company = (
        db.scalar(select(Operator).where(Operator.id == user.operator_id).with_for_update())
        if user and user.role == "operator" and user.operator_id
        else None
    )
    company_disabled = user and user.role == "operator" and (not company or not company.enabled)
    if not user or not user.enabled or not valid or company_disabled:
        raise HTTPException(401, "账号或密码错误")
    return create_session(db, user, response)


def create_session(db, user, response):
    from app.security import clear_account

    if user.role_profile_id:
        from app.models import RoleProfile

        db.scalar(
            select(RoleProfile).where(RoleProfile.id == user.role_profile_id).with_for_update()
        )
    clear_account(db, user.username)
    token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(32)
    db.add(
        LoginSession(
            token_hash=token_hash(token),
            user_id=user.id,
            expires_at=datetime.now(UTC) + timedelta(hours=8),
            csrf=csrf,
        )
    )
    db.commit()
    response.set_cookie(
        "ronghub_session",
        token,
        httponly=True,
        secure=not settings.demo_mode,
        samesite="lax",
        max_age=28800,
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"username": user.username, "role": user.role, "csrf_token": csrf}


@router.get("/api/v1/auth/me", response_model=CurrentUser)
def me(request: Request, user: Actor, db: DB, response: Response):
    login = db.get(LoginSession, token_hash(request.cookies["ronghub_session"]))
    response.headers["Cache-Control"] = "no-store"
    return {
        "username": user.username,
        "role": user.role,
        "csrf_token": login.csrf,
        "operator_id": user.operator_id,
        "role_profile_id": user.role_profile_id,
        "allowed_districts": user.allowed_districts,
        "demo_mode": settings.demo_mode,
        "must_change_password": user.must_change_password,
        "permissions": sorted(permissions(db, user)),
    }


@router.post("/api/v1/auth/logout", response_model=OkResponse)
def logout(request: Request, response: Response, user: Actor, db: DB):
    login = db.get(LoginSession, token_hash(request.cookies["ronghub_session"]))
    db.delete(login)
    db.commit()
    response.delete_cookie("ronghub_session", path="/")
    return {"ok": True}
