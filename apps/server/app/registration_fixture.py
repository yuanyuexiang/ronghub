"""Local-only registration browser data with strict name and identity guards."""

import argparse
import json
import secrets
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.engine import make_url

from app.config import settings
from app.db import SessionLocal
from app.models import (
    Audit,
    EnrollmentSession,
    LoginSession,
    Operator,
    Registration,
    RegistrationAction,
    RegistrationAttachment,
    RoleProfile,
    User,
)


def main():
    if not settings.demo_mode or make_url(settings.database_url).host not in {
        "127.0.0.1",
        "localhost",
    }:
        raise SystemExit("Requires a local demo database")
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["role", "clean"])
    p.add_argument("--role", type=int)
    args = p.parse_args()
    with SessionLocal.begin() as db:
        if args.action == "role":
            key = secrets.token_hex(8)
            company = "浏览器注册-" + key
            role = RoleProfile(
                name=company + "角色",
                base_role="operator",
                permissions=["stations.read", "companies.read", "companies.write"],
            )
            db.add(role)
            db.flush()
            print(
                json.dumps(
                    {
                        "role_id": role.id,
                        "company_name": company,
                        "username": "enroll-browser-" + key,
                        "credit_code": "A" + secrets.token_hex(9).upper()[:17],
                    },
                    ensure_ascii=False,
                )
            )
            return
        role = db.get(RoleProfile, args.role)
        if not role or not role.name.startswith("浏览器注册-") or not role.name.endswith("角色"):
            raise SystemExit("Refusing to remove a non-fixture role")
        company_name = role.name.removesuffix("角色")
        r = db.scalar(select(Registration).where(Registration.company_name == company_name))
        if r:
            if not r.username.startswith("enroll-browser-"):
                raise SystemExit("Refusing to remove a non-fixture applicant")
            user = db.get(User, r.user_id) if r.user_id else None
            company = db.get(Operator, r.operator_id) if r.operator_id else None
            if user and (user.username != r.username or user.role_profile_id != role.id):
                raise SystemExit("Unexpected business account")
            if company and company.name != company_name:
                raise SystemExit("Unexpected business company")
            db.execute(delete(EnrollmentSession).where(EnrollmentSession.registration_id == r.id))
            for a in db.scalars(
                select(RegistrationAttachment).where(RegistrationAttachment.registration_id == r.id)
            ):
                (Path(settings.storage_path).resolve() / a.storage_key).unlink(missing_ok=True)
                db.delete(a)
            db.flush()
            db.execute(delete(RegistrationAction).where(RegistrationAction.registration_id == r.id))
            db.execute(
                delete(Audit).where(Audit.object_id == r.id, Audit.action.like("registration.%"))
            )
            db.delete(r)
            db.flush()
            if user:
                db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
                db.execute(delete(Audit).where(Audit.user_id == user.id))
                db.delete(user)
                db.flush()
            if company:
                db.execute(
                    delete(Audit).where(
                        Audit.object_id == company.id, Audit.action.like("company.%")
                    )
                )
                db.delete(company)
        db.execute(delete(Audit).where(Audit.object_id == role.id, Audit.action.like("role.%")))
        db.delete(role)


if __name__ == "__main__":
    main()
