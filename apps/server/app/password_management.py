"""Authenticated password updates and scoped administrator recovery; no external delivery."""

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert

from app.dependencies import passwords, token_hash
from app.models import Audit, LoginSession, LoginThrottle, Operator, now
from app.security import clear_account
from app.sessions import revoke_users


def attempt(db, actor_id, purpose):
    key = token_hash(f"password-action:{purpose}:{actor_id}")
    current = now()
    count = db.scalar(
        insert(LoginThrottle)
        .values(key=key, attempts=1, window_end=current + timedelta(minutes=15))
        .on_conflict_do_update(
            index_elements=["key"],
            set_={
                "attempts": case(
                    (LoginThrottle.window_end <= current, 1), else_=LoginThrottle.attempts + 1
                ),
                "window_end": case(
                    (LoginThrottle.window_end <= current, current + timedelta(minutes=15)),
                    else_=LoginThrottle.window_end,
                ),
            },
        )
        .returning(LoginThrottle.attempts)
    )
    db.commit()  # Persist failed verification attempts before business row locks.
    if count > 5:
        raise HTTPException(429, "验证尝试过多，请15分钟后重试", headers={"Retry-After": "900"})
    return key


def session_active(db, request, user):
    token = token_hash(request.cookies.get("ronghub_session", ""))
    login = db.get(LoginSession, token, populate_existing=True)
    if not login or login.user_id != user.id or login.expires_at <= now():
        raise HTTPException(401, "会话已失效，请重新登录")
    if not user.enabled:
        raise HTTPException(401, "账号已停用")
    if user.role == "operator":
        company = db.scalar(
            select(Operator).where(Operator.id == user.operator_id).with_for_update()
        )
        if not company or not company.enabled:
            raise HTTPException(401, "企业已停用")


def set_password(db, actor, target, password, required, action, reason, attempt_key):
    if not password.strip():
        raise HTTPException(422, "密码不能全部为空白")
    if passwords.verify(password, target.password_hash):
        raise HTTPException(422, "新密码不能与当前密码相同")
    target.password_hash = passwords.hash(password)
    target.must_change_password = required
    target.version += 1
    revoke_users(db, [target.id])
    # Verified credential replacement releases account-specific failures, never the IP window.
    clear_account(db, target.username)
    clear_account(db, "enroll:" + target.username)
    keys = [attempt_key, token_hash(f"password-action:change:{target.id}")]
    db.execute(delete(LoginThrottle).where(LoginThrottle.key.in_(keys)))
    db.add(
        Audit(
            user_id=actor.id,
            action=action,
            object_id=target.id,
            detail={
                "target_username": target.username,
                "reason": reason,
                "must_change_password": required,
                "version": target.version,
            },
        )
    )
