from sqlalchemy import delete, select

from app.models import (
    EnrollmentSession,
    LoginSession,
    Registration,
    SmsChallenge,
    StaffApplicantSession,
    StaffApplication,
)


def revoke_users(db, user_ids):
    db.execute(
        delete(StaffApplicantSession).where(
            StaffApplicantSession.application_id.in_(
                select(StaffApplication.id).where(StaffApplication.user_id.in_(user_ids))
            )
        )
    )
    db.execute(delete(SmsChallenge).where(SmsChallenge.user_id.in_(user_ids)))
    db.execute(delete(LoginSession).where(LoginSession.user_id.in_(user_ids)))
    db.execute(
        delete(EnrollmentSession).where(
            EnrollmentSession.registration_id.in_(
                select(Registration.id).where(Registration.user_id.in_(user_ids))
            )
        )
    )
