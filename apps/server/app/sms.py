"""Purpose-bound OTP foundation; the explicit demonstration adapter never sends messages."""

import secrets
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.dependencies import token_hash
from app.models import LoginThrottle, SmsChallenge, now

COOKIE = "ronghub_sms"


class DemoSmsAdapter:
    mode = "demo"

    def send(self, destination, code, purpose):
        # No network, log, HTTP answer or plaintext message store.
        return secrets.token_urlsafe(24)


def adapter():
    if settings.sms_mode == "demo" and settings.demo_mode:
        return DemoSmsAdapter()
    raise HTTPException(503, "短信通道尚未配置，暂不能获取短信验证码")


def mask(phone):
    return phone[:3] + "****" + phone[-4:] if phone else None


def rate(db, scope, value, limit, seconds, message="短信验证操作过于频繁，请稍后重试"):
    stamp = now()
    key = token_hash("sms:" + scope + ":" + str(value))
    expired = LoginThrottle.window_end <= stamp
    count = db.scalar(
        insert(LoginThrottle)
        .values(key=key, attempts=1, window_end=stamp + timedelta(seconds=seconds))
        .on_conflict_do_update(
            index_elements=["key"],
            set_={
                "attempts": case((expired, 1), else_=LoginThrottle.attempts + 1),
                "window_end": case(
                    (expired, stamp + timedelta(seconds=seconds)), else_=LoginThrottle.window_end
                ),
            },
        )
        .returning(LoginThrottle.attempts)
    )
    db.commit()
    if count > limit:
        raise HTTPException(429, message, headers={"Retry-After": str(seconds)})


def issue(
    db, request, response, user, phone, purpose, *, subject=None, phone_version=None, public=False
):
    sender = adapter()
    stamp = now()
    last = db.scalar(
        select(SmsChallenge)
        .where(SmsChallenge.user_id == (user.id if user else None), SmsChallenge.purpose == purpose)
        .order_by(SmsChallenge.created_at.desc())
        .limit(1)
    )
    if not public and last and last.created_at > stamp - timedelta(seconds=60):
        raise HTTPException(429, "请等待60秒后再获取验证码", headers={"Retry-After": "60"})
    binding = request.cookies.get(COOKIE)
    if not binding or not 40 <= len(binding) <= 100:
        binding = secrets.token_urlsafe(40)
    token = secrets.token_urlsafe(32)
    code = f"{secrets.randbelow(1000000):06d}"
    receipt = sender.send(phone, code, purpose) if user and phone else secrets.token_urlsafe(24)
    db.execute(delete(SmsChallenge).where(SmsChallenge.expires_at < stamp - timedelta(hours=1)))
    pending = select(SmsChallenge).where(
        SmsChallenge.purpose == purpose, SmsChallenge.consumed_at.is_(None)
    )
    if user:
        pending = pending.where(SmsChallenge.user_id == user.id)
    else:
        pending = pending.where(
            SmsChallenge.user_id.is_(None),
            SmsChallenge.subject_hash == subject,
            SmsChallenge.binding_hash == token_hash(binding),
        )
    for old in db.scalars(pending):
        old.consumed_at = stamp
    db.add(
        SmsChallenge(
            token_hash=token_hash(token),
            binding_hash=token_hash(binding),
            user_id=user.id if user else None,
            subject_hash=subject,
            phone_version=phone_version,
            phone=phone,
            purpose=purpose,
            code_hash=token_hash(token + ":" + code),
            credential_version=user.version if user else None,
            expires_at=stamp + timedelta(minutes=5),
            attempts=0,
            delivery_mode=sender.mode,
            receipt_id=receipt,
        )
    )
    response.set_cookie(
        COOKIE,
        binding,
        httponly=True,
        secure=not settings.demo_mode,
        samesite="lax",
        path="/api/v1/auth",
        max_age=600,
    )
    response.headers["Cache-Control"] = "no-store"
    return {
        "challenge_id": token,
        "expires_at": (stamp + timedelta(minutes=5)).isoformat(),
        "expires_in_seconds": 300,
        "retry_after_seconds": 60,
        "delivery_mode": sender.mode,
        "message": "若账号已绑定有效手机，将生成相应用途的验证码。当前为模拟通道，未发送真实短信。"
        if public
        else "模拟短信已生成，未发送真实短信；请通过本地模拟验证工具获取验证码。",
    }


def consume(db, request, user, token, code, purpose):
    row = db.scalar(
        select(SmsChallenge).where(SmsChallenge.token_hash == token_hash(token)).with_for_update()
    )
    binding = request.cookies.get(COOKIE, "")
    if (
        not row
        or not binding
        or row.user_id != user.id
        or row.purpose != purpose
        or not secrets.compare_digest(row.binding_hash, token_hash(binding))
    ):
        raise HTTPException(422, "短信验证码无效或已过期")
    if (
        row.consumed_at is not None
        or row.expires_at <= now()
        or row.credential_version != user.version
        or row.attempts >= 5
    ):
        row.consumed_at = row.consumed_at or now()
        db.commit()
        raise HTTPException(422, "短信验证码无效或已过期")
    row.attempts += 1
    if not secrets.compare_digest(row.code_hash, token_hash(token + ":" + code)):
        if row.attempts >= 5:
            row.consumed_at = now()
        db.commit()
        raise HTTPException(422, "短信验证码无效或已过期")
    row.consumed_at = now()
    return row
