"""Remove only explicitly named local browser staff fixtures, never customer requests."""

import argparse

from sqlalchemy import delete
from sqlalchemy.engine import make_url

from app.config import settings
from app.db import SessionLocal
from app.models import Audit, RoleProfile, StaffApplication, User
from app.sessions import revoke_users


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--application", type=int)
    parser.add_argument("--profile", type=int)
    args = parser.parse_args()
    if not settings.demo_mode or make_url(settings.database_url).host not in {
        "localhost",
        "127.0.0.1",
    }:
        raise SystemExit("Staff browser fixture cleanup requires a local demo database")
    with SessionLocal.begin() as db:
        row = db.get(StaffApplication, args.application) if args.application else None
        profile = db.get(RoleProfile, args.profile) if args.profile else None
        if args.application and (
            not row or not row.is_demo or not row.applicant_name.startswith("浏览器工作人员-")
        ):
            raise SystemExit("Refusing to delete a non-fixture staff request")
        if args.profile and (not profile or not profile.name.startswith("browser-staff-role-")):
            raise SystemExit("Refusing to delete a non-fixture role")
        target = db.get(User, row.user_id) if row and row.user_id else None
        if target and (
            not target.username.startswith("staff-browser-")
            or not profile
            or target.role_profile_id != profile.id
        ):
            raise SystemExit("Refusing to delete a non-fixture account")
        if row:
            db.execute(
                delete(Audit).where(
                    Audit.action.in_(["staff_application.approve", "staff_application.reject"]),
                    Audit.object_id == row.id,
                )
            )
            db.delete(row)
            db.flush()
        if target:
            revoke_users(db, [target.id])
            db.execute(delete(Audit).where(Audit.user_id == target.id))
            db.delete(target)
            db.flush()
        if profile:
            db.execute(
                delete(Audit).where(
                    Audit.action.in_(["role.create", "role.update"]), Audit.object_id == profile.id
                )
            )
            db.delete(profile)
    print("Verified staff browser fixtures removed.")


if __name__ == "__main__":
    main()
