"""Anonymous SMS login/recovery; no account eligibility or OTP answer in issuance responses."""

import re
import secrets
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, text

from app import captcha, password_management, sms
from app.config import settings
from app.contracts import ErrorResponse, LoginResponse, PasswordRecovered, SmsAuthOptions, SmsIssued
from app.dependencies import DB, token_hash
from app.models import Audit, Operator, SmsChallenge, User, VerifiedPhone, now
from app.routers.auth import create_session

router = APIRouter(prefix="/api/v1/auth/sms", tags=["sms-authentication"])
UNAVAILABLE = "短信验证码无效或已过期"
PROVIDER_ERRORS = {
    503: {"model": ErrorResponse, "description": "SMS provider unavailable; no demo fallback"}
}


class IssueInput(BaseModel):
    purpose: Literal["login", "recovery"]
    identifier_kind: Literal["account", "phone"] = "account"
    identifier: str = Field(min_length=1, max_length=100)
    captcha_id: str = Field(min_length=32, max_length=128)
    captcha_answer: str = Field(min_length=1, max_length=12, repr=False)

    @model_validator(mode="after")
    def normalize_identifier(self):
        self.identifier = self.identifier.strip()
        if self.identifier_kind == "phone":
            self.identifier = self.identifier.replace(" ", "").removeprefix("+86")
            if not re.fullmatch(r"1[3-9][0-9]{9}", self.identifier):
                raise ValueError("请填写有效的中国大陆手机号")
        elif not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", self.identifier):
            raise ValueError("请填写有效账号")
        return self


class ConfirmInput(BaseModel):
    challenge_id: str = Field(min_length=32, max_length=128)
    code: str = Field(pattern=r"^[0-9]{6}$", repr=False)


class RecoverInput(ConfirmInput):
    new_password: str = Field(min_length=12, max_length=200, repr=False)

    @model_validator(mode="after")
    def nonblank_password(self):
        if not self.new_password.strip():
            raise ValueError("密码不能全部为空白")
        return self


@router.get("/options", response_model=SmsAuthOptions)
def options():
    return {
        "sms_mode": settings.sms_mode if settings.demo_mode else "disabled",
        "demo_mode": settings.demo_mode,
    }


def active(db, user):
    if not user or not user.enabled or user.role not in {"regulator", "association", "operator"}:
        return False
    if user.role == "operator":
        company = db.scalar(
            select(Operator).where(Operator.id == user.operator_id).with_for_update()
        )
        if not company or not company.enabled:
            return False
    return True


def usable_phone(binding):
    # A simulation record is usable only inside the same explicit demo transport boundary.
    return (
        binding is not None
        and settings.demo_mode
        and settings.sms_mode == "demo"
        and binding.verification_mode == "demo"
    )


@router.post("/challenges", response_model=SmsIssued, status_code=201, responses=PROVIDER_ERRORS)
def issue(body: IssueInput, request: Request, response: Response, db: DB):
    sms.adapter()
    captcha.verify(db, request, body.captcha_id, body.captcha_answer)
    address = request.client.host if request.client else "unknown"
    subject = body.purpose + ":" + body.identifier_kind + ":" + body.identifier
    sms.rate(db, "public-issue-ip", address, 20, 3600)
    sms.rate(db, "public-issue-target", subject, 5, 3600)
    sms.rate(db, "public-issue-cooldown", subject, 1, 60)
    db.execute(text("SELECT pg_advisory_xact_lock(78230012)"))
    stmt = select(User)
    if body.identifier_kind == "account":
        stmt = stmt.where(User.username == body.identifier)
    else:
        stmt = stmt.join(VerifiedPhone, VerifiedPhone.user_id == User.id).where(
            VerifiedPhone.phone == body.identifier
        )
    user = db.scalar(
        stmt.with_for_update(of=User, key_share=True).execution_options(populate_existing=True)
    )
    binding = db.get(VerifiedPhone, user.id, with_for_update=True) if user else None
    eligible = (
        active(db, user)
        and usable_phone(binding)
        and not (body.purpose == "login" and user.must_change_password)
    )
    result = sms.issue(
        db,
        request,
        response,
        user if eligible else None,
        binding.phone if eligible else None,
        body.purpose,
        subject=token_hash(subject),
        phone_version=binding.version if eligible else None,
        public=True,
    )
    db.commit()
    return result


def verified_subject(db, request, body, purpose):
    sms.adapter()
    sms.rate(db, "public-confirm-ip", request.client.host if request.client else "unknown", 60, 300)
    db.execute(text("SELECT pg_advisory_xact_lock(78230012)"))
    candidate = db.get(SmsChallenge, token_hash(body.challenge_id), populate_existing=True)
    cookie = request.cookies.get(sms.COOKIE, "")
    if (
        not candidate
        or candidate.user_id is None
        or candidate.purpose != purpose
        or not cookie
        or not secrets.compare_digest(candidate.binding_hash, token_hash(cookie))
    ):
        raise HTTPException(422, UNAVAILABLE)
    user = db.scalar(
        select(User)
        .where(User.id == candidate.user_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    binding = (
        db.get(VerifiedPhone, user.id, with_for_update=True, populate_existing=True)
        if user
        else None
    )
    valid = (
        active(db, user)
        and usable_phone(binding)
        and candidate.phone == binding.phone
        and candidate.phone_version == binding.version
        and not (purpose == "login" and user.must_change_password)
    )
    if not valid:
        candidate.consumed_at = candidate.consumed_at or now()
        db.commit()
        raise HTTPException(422, UNAVAILABLE)
    sms.consume(db, request, user, body.challenge_id, body.code, purpose)
    return user


@router.post("/login", response_model=LoginResponse, responses=PROVIDER_ERRORS)
def login(body: ConfirmInput, request: Request, response: Response, db: DB):
    user = verified_subject(db, request, body, "login")
    db.add(
        Audit(
            user_id=user.id,
            action="auth.sms.login",
            object_id=user.id,
            detail={"delivery_mode": "demo"},
        )
    )
    result = create_session(db, user, response)
    response.delete_cookie(sms.COOKIE, path="/api/v1/auth")
    return result


@router.post("/recover", response_model=PasswordRecovered, responses=PROVIDER_ERRORS)
def recover(body: RecoverInput, request: Request, response: Response, db: DB):
    user = verified_subject(db, request, body, "recovery")
    password_management.set_password(
        db,
        user,
        user,
        body.new_password,
        False,
        "password.recover",
        "本人通过绑定手机验证码恢复",
        token_hash("sms-recovery:" + str(user.id)),
    )
    db.commit()
    response.delete_cookie("ronghub_session", path="/")
    response.delete_cookie(sms.COOKIE, path="/api/v1/auth")
    return {"ok": True, "reauthenticate": True}
