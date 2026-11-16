"""Temporary company accounts for localhost browser verification only."""

import argparse
import json
import secrets

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.engine import make_url

from app.config import settings
from app.db import SessionLocal
from app.models import Audit, LoginSession, Operator, RoleProfile, Station, User


def main():
    if not settings.demo_mode or make_url(settings.database_url).host not in {
        "localhost",
        "127.0.0.1",
    }:
        raise SystemExit("Company browser fixtures require a local demo database")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["create", "clean"])
    parser.add_argument("--company", type=int)
    parser.add_argument("--profile", type=int)
    parser.add_argument("--with-stations", action="store_true")
    args = parser.parse_args()
    with SessionLocal.begin() as db:
        if args.action == "create":
            source = db.scalar(select(User).where(User.username == "operator_a"))
            if not source:
                raise SystemExit("Seed demo accounts first")
            key = secrets.token_hex(8)
            company = Operator(name="浏览器企业-" + key)
            db.add(company)
            db.flush()
            user = User(
                username="browser-company-" + key,
                password_hash=source.password_hash,
                role="operator",
                operator_id=company.id,
            )
            db.add(user)
            db.flush()
            stations = []
            if args.with_stations:
                for index, district in enumerate(["鼓楼区", "晋安区"]):
                    station = Station(
                        operator_id=company.id,
                        source="browser-company",
                        external_id=key + str(index),
                        name=company.name + "-" + district,
                        address="临时演示",
                        district=district,
                        status="operating",
                        power_kw=60,
                        connectors=1,
                    )
                    db.add(station)
                    db.flush()
                    stations.append({"id": station.id, "name": station.name, "district": district})
            print(
                json.dumps(
                    {
                        "id": company.id,
                        "name": company.name,
                        "username": user.username,
                        "stations": stations,
                    },
                    ensure_ascii=False,
                )
            )
        else:
            company = db.get(Operator, args.company)
            if not company or not company.name.startswith("浏览器企业-"):
                raise SystemExit("Refusing to remove a non-fixture company")
            users = list(db.scalars(select(User).where(User.operator_id == company.id)))
            if not users or any(not u.username.startswith("browser-company-") for u in users):
                raise SystemExit("Refusing to remove non-fixture users")
            uids = [u.id for u in users]
            profile = db.get(RoleProfile, args.profile) if args.profile else None
            if args.profile and (not profile or not profile.name.startswith(company.name)):
                raise SystemExit("Refusing to remove a non-fixture role")
            if profile:
                db.execute(
                    delete(Audit).where(
                        Audit.object_id == profile.id,
                        Audit.action.in_(["role.create", "role.update"]),
                    )
                )
            db.execute(
                delete(Audit).where(
                    Audit.object_id.in_(uids),
                    Audit.action.in_(
                        ["account.create", "account.update", "password.reset", "password.change"]
                    ),
                )
            )
            db.execute(delete(LoginSession).where(LoginSession.user_id.in_(uids)))
            db.execute(
                delete(Audit).where(
                    or_(
                        Audit.user_id.in_(uids),
                        and_(
                            Audit.object_id == company.id,
                            Audit.action.in_(["company.update", "company.status"]),
                        ),
                    )
                )
            )
            db.execute(delete(User).where(User.id.in_(uids)))
            stations = list(db.scalars(select(Station).where(Station.operator_id == company.id)))
            if any(
                s.source != "browser-company" or not s.name.startswith(company.name)
                for s in stations
            ):
                raise SystemExit("Refusing to remove non-fixture stations")
            for station in stations:
                db.delete(station)
            db.flush()
            db.delete(company)
            if profile:
                db.delete(profile)


if __name__ == "__main__":
    main()
