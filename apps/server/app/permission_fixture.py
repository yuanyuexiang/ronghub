"""Strictly local temporary staff accounts for fine-grained UI verification."""

import argparse
import json
import secrets

from sqlalchemy import delete, select
from sqlalchemy.engine import make_url

from app.config import settings
from app.db import SessionLocal
from app.models import Audit, RoleProfile, User
from app.sessions import revoke_users


def main():
    if not settings.demo_mode or make_url(settings.database_url).host not in {
        "localhost",
        "127.0.0.1",
    }:
        raise SystemExit("Permission fixtures require a local demo database")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["create", "clean"])
    parser.add_argument(
        "--module",
        choices=["subsidy", "applications", "ratings", "inspections", "alerts", "content"],
        default="subsidy",
    )
    parser.add_argument(
        "--kind",
        choices=[
            "review",
            "adjust",
            "disburse",
            "create",
            "edit",
            "materials",
            "submit",
            "withdraw",
            "publish",
            "delist",
            "standards",
            "assign",
            "reply",
            "return",
            "close",
            "rules",
            "samples",
            "acknowledge",
            "manage",
        ],
        default="review",
    )
    parser.add_argument("--user", type=int)
    args = parser.parse_args()
    with SessionLocal.begin() as db:
        if args.action == "create":
            allowed = {
                "inspections": {"assign", "materials", "reply", "return", "close"},
                "content": {"manage", "create", "edit", "publish", "withdraw"},
                "alerts": {"rules", "samples", "acknowledge"},
                "subsidy": {"review", "adjust", "disburse"},
                "ratings": {
                    "create",
                    "edit",
                    "materials",
                    "submit",
                    "withdraw",
                    "review",
                    "publish",
                    "delist",
                    "standards",
                },
                "applications": {"create", "edit", "materials", "submit", "withdraw", "review"},
            }
            if args.kind not in allowed[args.module]:
                raise SystemExit("Unsupported fixture action")
            base = "regulator"
            if args.module == "applications" and args.kind != "review":
                base = "operator"
            if args.module == "inspections" and args.kind in {"materials", "reply"}:
                base = "operator"
            if args.module == "ratings":
                base = (
                    "association"
                    if args.kind in {"review", "publish", "delist", "standards"}
                    else "operator"
                )
            source = db.scalar(
                select(User).where(
                    User.username
                    == {
                        "regulator": "regulator",
                        "association": "association",
                        "operator": "operator_a",
                    }[base]
                )
            )
            if source is None:
                raise SystemExit("Seed demo regulator first")
            name = "browser-permission-" + secrets.token_hex(8)
            profile = RoleProfile(
                name=name,
                base_role=base,
                permissions=sorted(
                    {"stations.read", args.module + ".read", args.module + "." + args.kind}
                    | ({"content.manage"} if args.module == "content" else set())
                ),
            )
            db.add(profile)
            db.flush()
            user = User(
                username=name,
                password_hash=source.password_hash,
                role=base,
                operator_id=source.operator_id if base == "operator" else None,
                role_profile_id=profile.id,
            )
            db.add(user)
            db.flush()
            print(json.dumps({"id": user.id, "username": user.username, "profile_id": profile.id}))
        else:
            user = db.get(User, args.user)
            profile = db.get(RoleProfile, user.role_profile_id) if user else None
            if (
                not user
                or not user.username.startswith("browser-permission-")
                or user.role not in {"regulator", "operator", "association"}
                or (user.role in {"regulator", "association"} and user.operator_id is not None)
                or (user.role == "operator" and user.operator_id is None)
                or not profile
                or profile.name != user.username
                or profile.base_role != user.role
            ):
                raise SystemExit("Refusing to delete non-fixture staff")
            revoke_users(db, [user.id])
            db.execute(delete(Audit).where(Audit.user_id == user.id))
            db.delete(user)
            db.flush()
            db.delete(profile)


if __name__ == "__main__":
    main()
