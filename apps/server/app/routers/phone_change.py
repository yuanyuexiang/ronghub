"""Two-phone proof exchange; existing account contacts cannot establish identity."""

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app import password_management as management
from app import sms
from app.contracts import PhoneBound, SmsIssued
from app.dependencies import DB, Actor, passwords
from app.models import Audit, SmsChallenge, VerifiedPhone, now
from app.routers.phone import BindConfirm, BindRequest, fresh_actor
from app.sessions import revoke_users

router = APIRouter(prefix="/api/v1/auth/phone/change", tags=["phone"])
OLD = "phone_change_old"
NEW = "phone_change_new"


class ChangeStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=200, repr=False)
    phone_version: int = Field(strict=True, ge=1, le=2147483647)


class ChangeNew(BaseModel):
    model_config = ConfigDict(extra="forbid")
    old_challenge_id: str = Field(min_length=32, max_length=128)
    old_code: str = Field(pattern=r"^[0-9]{6}$", repr=False)
    phone: str = Field(pattern=r"^1[3-9][0-9]{9}$")

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone(cls, value):
        return BindRequest.normalize_phone(value)


class ChangeConfirm(BindConfirm):
    model_config = ConfigDict(extra="forbid")


def binding(db, user):
    value = db.scalar(
        select(VerifiedPhone)
        .where(VerifiedPhone.user_id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not value:
        raise HTTPException(409, "账号尚未绑定手机，请先完成首次验证")
    return value


def check_old(proof, phone, user):
    if (
        proof.phone_version != phone.version
        or proof.phone != phone.phone
        or proof.credential_version != user.version
    ):
        raise HTTPException(409, "手机或账号状态已变化，请重新核验")


def audit(db, user, action, **detail):
    db.add(Audit(user_id=user.id, action=action, object_id=user.id, detail=detail))


def throttle(db, request, user, scope, limit):
    sms.adapter()
    sms.rate(
        db, scope + "-ip", request.client.host if request.client else "unknown", limit * 4, 3600
    )
    sms.rate(db, scope + "-user", user.id, limit, 3600)


@router.post("/challenges", response_model=SmsIssued, status_code=201)
def start(body: ChangeStart, request: Request, response: Response, user: Actor, db: DB):
    throttle(db, request, user, "phone-change-start", 5)
    management.attempt(db, user.id, "phone-change")
    user = fresh_actor(db, request, user)
    if not passwords.verify(body.current_password, user.password_hash):
        raise HTTPException(403, "当前密码不正确")
    phone = binding(db, user)
    if phone.version != body.phone_version:
        raise HTTPException(409, "手机验证状态已变化，请刷新后重新核对")
    # Restart revokes the entire prior exchange, including already-verified parent proofs.
    db.execute(
        update(SmsChallenge)
        .where(SmsChallenge.user_id == user.id, SmsChallenge.purpose.in_([OLD, NEW]))
        .values(expires_at=now())
    )
    result = SmsIssued.model_validate(
        sms.issue(db, request, response, user, phone.phone, OLD, phone_version=phone.version)
    )
    audit(
        db,
        user,
        "phone.change.start",
        phone_version=phone.version,
        delivery_mode=result.delivery_mode,
    )
    db.commit()
    return result


@router.post("/new-challenges", response_model=SmsIssued, status_code=201)
def issue_new(body: ChangeNew, request: Request, response: Response, user: Actor, db: DB):
    throttle(db, request, user, "phone-change-new", 10)
    user = fresh_actor(db, request, user)
    phone = binding(db, user)
    old = sms.consume(db, request, user, body.old_challenge_id, body.old_code, OLD)
    check_old(old, phone, user)
    if body.phone == phone.phone:
        raise HTTPException(422, "新手机号不能与当前绑定手机相同")
    if db.scalar(select(VerifiedPhone.user_id).where(VerifiedPhone.phone == body.phone)):
        raise HTTPException(409, "无法绑定此手机号，请联系管理员核验")
    result = SmsIssued.model_validate(
        sms.issue(
            db,
            request,
            response,
            user,
            body.phone,
            NEW,
            subject=old.token_hash,
            phone_version=phone.version,
        )
    )
    audit(
        db,
        user,
        "phone.change.new",
        phone_version=phone.version,
        delivery_mode=result.delivery_mode,
    )
    db.commit()
    return result


@router.post("/confirm", response_model=PhoneBound)
def confirm(body: ChangeConfirm, request: Request, response: Response, user: Actor, db: DB):
    throttle(db, request, user, "phone-change-confirm", 20)
    user = fresh_actor(db, request, user)
    phone = binding(db, user)
    new = sms.consume(db, request, user, body.challenge_id, body.code, NEW)
    old = db.scalar(
        select(SmsChallenge)
        .where(SmsChallenge.token_hash == new.subject_hash)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        not old
        or old.user_id != user.id
        or old.purpose != OLD
        or old.consumed_at is None
        or old.expires_at <= now()
        or old.binding_hash != new.binding_hash
        or old.delivery_mode != new.delivery_mode
    ):
        raise HTTPException(422, "原手机核验已失效，请重新开始换绑")
    check_old(old, phone, user)
    if new.phone_version != phone.version or not new.phone or new.phone == phone.phone:
        raise HTTPException(409, "手机验证状态已变化，请重新核验")
    before = phone.version
    phone.phone, phone.verified_at = new.phone, now()
    phone.verification_mode = new.delivery_mode
    phone.version += 1
    user.version += 1
    audit(
        db,
        user,
        "phone.change.confirm",
        before_phone_version=before,
        phone_version=phone.version,
        credential_version=user.version,
        delivery_mode=new.delivery_mode,
    )
    try:
        db.flush()
        result = PhoneBound.model_validate(
            {
                "ok": True,
                "reauthenticate": True,
                "phone_masked": sms.mask(phone.phone),
                "verified_at": phone.verified_at.isoformat(),
            }
        )
        revoke_users(db, [user.id])
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "无法绑定此手机号，请联系管理员核验") from None
    response.headers["Cache-Control"] = "no-store"
    response.delete_cookie("ronghub_session", path="/")
    response.delete_cookie(sms.COOKIE, path="/api/v1/auth")
    return result
