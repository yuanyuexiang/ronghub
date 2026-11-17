"""Separate applicant sessions; no business identity is created before approval."""

import secrets
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select

from app.config import settings
from app.dependencies import DB, token_hash
from app.models import (
    EnrollmentSession,
    Operator,
    Registration,
    RegistrationAction,
    RegistrationAttachment,
    User,
    now,
)
from app.reporting import SnapshotDB

COOKIE = "ronghub_enrollment"


def demo_only():
    if not settings.demo_mode:
        raise HTTPException(409, "注册及材料审核规则暂限演示环境")


def applicant(request: Request, db: DB):
    demo_only()
    session = db.get(EnrollmentSession, token_hash(request.cookies.get(COOKIE, "")))
    if not session or session.expires_at <= now():
        raise HTTPException(401, "请先登录注册进度查询")
    registration = db.get(Registration, session.registration_id)
    if not registration:
        raise HTTPException(401, "注册申请不可用")
    if registration.status == "approved":
        user = db.get(User, registration.user_id)
        company = db.get(Operator, registration.operator_id)
        if user and user.must_change_password:
            raise HTTPException(403, "请先登录业务平台修改临时密码")
        if not user or not user.enabled or not company or not company.enabled:
            raise HTTPException(401, "账号或企业已停用")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not secrets.compare_digest(
        request.headers.get("x-enrollment-csrf", ""), session.csrf
    ):
        raise HTTPException(403, "注册请求校验失败")
    return registration


Applicant = Annotated[Registration, Depends(applicant)]


def snapshot_applicant(request: Request, db: SnapshotDB):
    return applicant(request, db)


SnapshotApplicant = Annotated[Registration, Depends(snapshot_applicant)]


def establish(db, response, registration):
    token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(32)
    db.add(
        EnrollmentSession(
            token_hash=token_hash(token),
            registration_id=registration.id,
            csrf=csrf,
            expires_at=now() + timedelta(hours=8),
        )
    )
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=not settings.demo_mode,
        samesite="lax",
        max_age=28800,
        path="/api/v1/enrollment",
    )
    return csrf


def lock(db, registration):
    return db.scalar(
        select(Registration)
        .where(Registration.id == registration.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def editable(registration):
    if registration.status not in {"draft", "rejected"}:
        raise HTTPException(409, "仅草稿或驳回申请可补充资料")


def output(r):
    return {
        "id": r.id,
        "username": r.username,
        "company_name": r.company_name,
        "credit_code": r.credit_code,
        "registration_address": r.registration_address,
        "contacts": r.contacts,
        "status": r.status,
        "version": r.version,
        "operator_id": r.operator_id,
        "user_id": r.user_id,
        "created_at": r.created_at.isoformat(),
        "is_demo": r.is_demo,
    }


def record(db, r, action, comment="", actor_id=None):
    db.flush()
    snapshot = output(r)
    snapshot["materials"] = [
        {"id": a.id, "sha256": a.sha256, "filename": a.filename}
        for a in db.scalars(
            select(RegistrationAttachment).where(RegistrationAttachment.registration_id == r.id)
        )
    ]
    db.add(
        RegistrationAction(
            registration_id=r.id,
            actor_id=actor_id,
            action=action,
            comment=comment,
            snapshot=snapshot,
        )
    )
