"""Shared private/public staff request projections and demo scope."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.config import settings
from app.dependencies import DB, token_hash
from app.models import StaffApplicantSession, StaffApplication, now
from app.sms import mask

COOKIE = "ronghub_staff_applicant"


def demo_only():
    if not settings.demo_mode:
        raise HTTPException(409, "个人账号申请暂限演示环境，正式审核及通知流程待确认")


def output(row, private=False):
    data = {
        "id": row.id,
        "requested_role": row.requested_role,
        "applicant_name": row.applicant_name,
        "phone_masked": mask(row.contact_phone),
        "status": row.status,
        "version": row.version,
        "created_at": row.created_at.isoformat(),
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "review_comment": row.review_comment,
        "username": row.username,
        "notification_status": "not_sent",
        "is_demo": row.is_demo,
    }
    if private:
        data.update(
            contact_phone=row.contact_phone, user_id=row.user_id, grant_snapshot=row.grant_snapshot
        )
    return data


def applicant(request: Request, db: DB):
    session = db.get(StaffApplicantSession, token_hash(request.cookies.get(COOKIE, "")))
    if not session or session.expires_at <= now():
        raise HTTPException(401, "当前浏览器没有有效申请查询凭据")
    row = db.get(StaffApplication, session.application_id)
    if not row:
        raise HTTPException(404, "申请不存在")
    return row


StaffApplicant = Annotated[StaffApplication, Depends(applicant)]
