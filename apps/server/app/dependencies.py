import hashlib
import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from app.db import session
from app.models import LoginSession, Operator, User

DB = Annotated[Session, Depends(session)]
passwords = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = passwords.hash(secrets.token_urlsafe(32))


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def current_user(request: Request, db: DB):
    token = request.cookies.get("ronghub_session", "")
    login = db.get(LoginSession, token_hash(token))
    if not login or login.expires_at <= datetime.now(UTC):
        raise HTTPException(401, "请先登录")
    user = db.get(User, login.user_id)
    if not user or not user.enabled:
        raise HTTPException(401, "账号已停用")
    if user.role == "operator":
        company = db.get(Operator, user.operator_id) if user.operator_id else None
        if not company or not company.enabled:
            raise HTTPException(401, "企业账号已停用或未关联企业")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not secrets.compare_digest(
        request.headers.get("x-csrf-token", ""), login.csrf
    ):
        raise HTTPException(403, "请求校验失败")
    if user.must_change_password and request.url.path not in {
        "/api/v1/auth/me",
        "/api/v1/auth/logout",
        "/api/v1/auth/password",
    }:
        raise HTTPException(403, "请先修改临时密码")
    from app.authorization import request_capability, require

    capability = request_capability(request)
    if capability:
        require(db, user, capability)
    from app.scopes import check_global_request

    check_global_request(request, user)
    return user


def scope(stmt, user):
    from app.scopes import station_scope

    return station_scope(stmt, user)


Actor = Annotated[User, Depends(current_user)]
