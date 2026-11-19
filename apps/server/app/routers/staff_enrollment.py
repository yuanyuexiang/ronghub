"""Public requests carry no role grants or verified-phone claims."""

import secrets
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import IntegrityError

from app import captcha, sms
from app import staff_applications as staff
from app.config import settings
from app.contracts import StaffApplicantStatus, StaffEnrollmentOptions
from app.dependencies import DB, token_hash
from app.models import StaffApplicantSession, StaffApplication, now

router = APIRouter(prefix="/api/v1/auth/staff-enrollment", tags=["staff-enrollment"])


class SubmitInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requested_role: Literal["regulator", "association"]
    applicant_name: str = Field(min_length=1, max_length=100)
    contact_phone: str = Field(pattern=r"^1[3-9][0-9]{9}$")
    captcha_id: str = Field(min_length=32, max_length=128)
    captcha_answer: str = Field(min_length=1, max_length=12, repr=False)

    @field_validator("applicant_name")
    @classmethod
    def name(cls, value):
        value = value.strip()
        if not value or any(not char.isprintable() for char in value):
            raise ValueError("请填写有效申请人姓名")
        return value

    @field_validator("contact_phone", mode="before")
    @classmethod
    def phone(cls, value):
        return (
            value.strip().replace(" ", "").removeprefix("+86") if isinstance(value, str) else value
        )


@router.get("/options", response_model=StaffEnrollmentOptions)
def options():
    return {"enabled": settings.demo_mode, "demo_mode": settings.demo_mode}


@router.post("", status_code=201, response_model=StaffApplicantStatus)
def submit(body: SubmitInput, request: Request, response: Response, db: DB):
    staff.demo_only()
    captcha.verify(db, request, body.captcha_id, body.captcha_answer)
    sms.rate(
        db,
        "staff-apply-ip",
        request.client.host if request.client else "unknown",
        5,
        3600,
        "申请提交过于频繁，请稍后重试",
    )
    sms.rate(
        db,
        "staff-apply-contact",
        body.requested_role + ":" + body.contact_phone,
        3,
        3600,
        "申请提交过于频繁，请稍后重试",
    )
    row = StaffApplication(
        requested_role=body.requested_role,
        applicant_name=body.applicant_name,
        contact_phone=body.contact_phone,
    )
    db.add(row)
    token = secrets.token_urlsafe(40)
    try:
        db.flush()
        db.add(
            StaffApplicantSession(
                token_hash=token_hash(token),
                application_id=row.id,
                expires_at=now() + timedelta(hours=24),
            )
        )
        result = StaffApplicantStatus.model_validate(staff.output(row))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "当前申请暂不能受理，请使用原浏览器查询或联系管理员核验") from None
    response.set_cookie(
        staff.COOKIE,
        token,
        httponly=True,
        secure=not settings.demo_mode or settings.secure_cookies,
        samesite="lax",
        path="/api/v1/auth/staff-enrollment",
        max_age=86400,
    )
    return result


@router.get("/me", response_model=StaffApplicantStatus)
def status(row: staff.StaffApplicant):
    return staff.output(row)
