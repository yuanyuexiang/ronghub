"""Self-service first phone binding; business contacts are never identity proofs."""

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app import password_management as management
from app import sms
from app.config import settings
from app.contracts import ErrorResponse, PhoneBound, PhoneState, SmsIssued
from app.dependencies import DB, Actor, passwords
from app.models import Audit, User, VerifiedPhone
from app.sessions import revoke_users

router = APIRouter(prefix="/api/v1/auth/phone", tags=["phone"])


class BindRequest(BaseModel):
    phone: str = Field(pattern=r"^1[3-9][0-9]{9}$")
    current_password: str = Field(min_length=1, max_length=200, repr=False)

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone(cls, value):
        return (
            value.strip().replace(" ", "").removeprefix("+86") if isinstance(value, str) else value
        )


class BindConfirm(BaseModel):
    challenge_id: str = Field(min_length=32, max_length=128)
    code: str = Field(pattern=r"^[0-9]{6}$", repr=False)


def fresh_actor(db, request, user):
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
    if fresh.must_change_password:
        raise HTTPException(403, "请先修改临时密码")
    return fresh


@router.get("", response_model=PhoneState)
def state(user: Actor, db: DB):
    binding = db.get(VerifiedPhone, user.id)
    return {
        "verified": binding is not None,
        "verification_mode": binding.verification_mode if binding else None,
        "phone_masked": sms.mask(binding.phone) if binding else None,
        "verified_at": binding.verified_at.isoformat() if binding else None,
        "version": binding.version if binding else None,
        "sms_mode": settings.sms_mode if settings.demo_mode else "disabled",
    }


@router.post(
    "/challenges",
    response_model=SmsIssued,
    status_code=201,
    responses={
        503: {
            "model": ErrorResponse,
            "description": "SMS provider unavailable; no implicit demo fallback",
        }
    },
)
def request_binding(body: BindRequest, request: Request, response: Response, user: Actor, db: DB):
    sms.adapter()
    management.attempt(db, user.id, "phone-bind")
    sms.rate(db, "bind-issue-ip", request.client.host if request.client else "unknown", 20, 3600)
    sms.rate(db, "bind-issue-user", user.id, 5, 3600)
    user = fresh_actor(db, request, user)
    if not passwords.verify(body.current_password, user.password_hash):
        raise HTTPException(403, "当前密码不正确")
    if db.get(VerifiedPhone, user.id):
        raise HTTPException(409, "账号已绑定手机，变更需另行核验")
    if db.scalar(select(VerifiedPhone.user_id).where(VerifiedPhone.phone == body.phone)):
        raise HTTPException(409, "无法绑定此手机号，请联系管理员核验")
    result = sms.issue(db, request, response, user, body.phone, "bind")
    db.add(
        Audit(
            user_id=user.id,
            action="phone.bind.request",
            object_id=user.id,
            detail={"delivery_mode": result["delivery_mode"]},
        )
    )
    db.commit()
    return result


@router.post(
    "/confirm",
    response_model=PhoneBound,
    responses={
        503: {
            "model": ErrorResponse,
            "description": "SMS provider unavailable; no implicit demo fallback",
        }
    },
)
def confirm_binding(body: BindConfirm, request: Request, response: Response, user: Actor, db: DB):
    sms.adapter()
    sms.rate(db, "bind-confirm-ip", request.client.host if request.client else "unknown", 60, 300)
    user = fresh_actor(db, request, user)
    challenge = sms.consume(db, request, user, body.challenge_id, body.code, "bind")
    if db.get(VerifiedPhone, user.id):
        raise HTTPException(409, "账号已绑定手机，变更需另行核验")
    binding = VerifiedPhone(
        user_id=user.id, phone=challenge.phone, verification_mode=challenge.delivery_mode
    )
    db.add(binding)
    user.version += 1
    db.add(
        Audit(
            user_id=user.id,
            action="phone.bind.confirm",
            object_id=user.id,
            detail={
                "verified": True,
                "version": user.version,
                "delivery_mode": challenge.delivery_mode,
            },
        )
    )
    try:
        db.flush()
        revoke_users(db, [user.id])
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "无法绑定此手机号，请联系管理员核验") from None
    response.delete_cookie("ronghub_session", path="/")
    response.delete_cookie(sms.COOKIE, path="/api/v1/auth")
    return {
        "ok": True,
        "reauthenticate": True,
        "phone_masked": sms.mask(binding.phone),
        "verified_at": binding.verified_at.isoformat(),
    }
