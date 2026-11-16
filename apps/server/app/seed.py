"""Explicit local demo data only. Password is supplied by the developer, never committed."""

import os

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.main import passwords
from app.models import Operator, Station, User


def main():
    if not settings.demo_mode:
        raise SystemExit("Seed requires DEMO_MODE=true")
    password = os.environ.get("DEMO_PASSWORD")
    if not password or len(password) < 12:
        raise SystemExit("Set DEMO_PASSWORD (at least 12 characters)")
    with SessionLocal.begin() as db:
        operator_ids = {}
        for index, (name, username) in enumerate(
            [("演示运营商甲", "operator_a"), ("演示运营商乙", "operator_b")], 1
        ):
            existing_user = db.scalar(select(User).where(User.username == username))
            operator = (
                db.get(Operator, existing_user.operator_id)
                if existing_user and existing_user.operator_id
                else db.scalar(select(Operator).where(Operator.name == name))
            )
            if operator is None:
                operator = Operator(name=name)
                db.add(operator)
                db.flush()
            operator_ids[index] = operator.id
        for name, role, oid in [
            ("regulator", "regulator", None),
            ("association", "association", None),
            ("operator_a", "operator", operator_ids[1]),
            ("operator_b", "operator", operator_ids[2]),
        ]:
            if not db.scalar(select(User).where(User.username == name)):
                db.add(
                    User(
                        username=name,
                        password_hash=passwords.hash(password),
                        role=role,
                        operator_id=oid,
                    )
                )
        for i, name in enumerate(
            ["演示·鼓楼公共充电站", "演示·仓山绿色充电站", "演示·晋安便民充电站"], 1
        ):
            coordinates = [
                ("119.296500", "26.074500"),
                ("119.310000", "26.020000"),
                ("119.340000", "26.090000"),
            ][i - 1]
            existing = db.scalar(
                select(Station).where(Station.source == "demo", Station.external_id == str(i))
            )
            if (
                existing
                and existing.longitude is None
                and existing.latitude is None
                and existing.coordinate_system == "unknown"
                and existing.name.startswith("演示·")
            ):
                existing.longitude, existing.latitude = coordinates
                existing.coordinate_system, existing.location_is_demo, existing.station_type = (
                    "wgs84",
                    True,
                    "charging",
                )
            if not existing:
                db.add(
                    Station(
                        source="demo",
                        external_id=str(i),
                        operator_id=operator_ids[1 if i < 3 else 2],
                        name=name,
                        district=["鼓楼区", "仓山区", "晋安区"][i - 1],
                        address="模拟地址，非真实场站",
                        longitude=coordinates[0],
                        latitude=coordinates[1],
                        coordinate_system="wgs84",
                        location_is_demo=True,
                        station_type="charging",
                        connectors=i * 6,
                        power_kw=i * 120,
                        status="pending" if i == 1 else "operating",
                    )
                )
    print("Demo seed ready; usernames: regulator, association, operator_a, operator_b")


def business_seed():
    from datetime import date
    from decimal import Decimal

    from app.models import AlertRule, EnergyRecord, RatingStandard, SubsidyPolicy

    if not settings.demo_mode:
        raise SystemExit("Demo mode required")
    with SessionLocal.begin() as db:
        if not db.scalar(
            select(SubsidyPolicy).where(
                SubsidyPolicy.code == "DEMO-KWH", SubsidyPolicy.version == 1
            )
        ):
            db.add(
                SubsidyPolicy(
                    code="DEMO-KWH",
                    version=1,
                    name="演示按电量补贴（非正式政策）",
                    rate=Decimal("0.10"),
                    effective_from=date(2026, 1, 1),
                    effective_to=date(2026, 12, 31),
                )
            )
        if not db.scalar(
            select(RatingStandard).where(
                RatingStandard.code == "DEMO-RATING", RatingStandard.version == 1
            )
        ):
            db.add(
                RatingStandard(
                    code="DEMO-RATING",
                    version=1,
                    name="演示星级标准（非正式标准）",
                    rules={
                        "indicators": [
                            {
                                "code": "service",
                                "name": "服务自评",
                                "weight": "60",
                                "max_score": "100",
                                "source": "manual",
                            },
                            {
                                "code": "safety",
                                "name": "安全材料自评",
                                "weight": "40",
                                "max_score": "100",
                                "source": "manual",
                            },
                        ],
                        "thresholds": [
                            {"minimum": "60", "stars": 1},
                            {"minimum": "80", "stars": 3},
                            {"minimum": "95", "stars": 5},
                        ],
                    },
                )
            )
        if not db.scalar(
            select(AlertRule).where(AlertRule.code == "DEMO-OFFLINE", AlertRule.version == 1)
        ):
            db.add(
                AlertRule(
                    code="DEMO-OFFLINE",
                    version=1,
                    name="演示离线时长（非实时监测）",
                    metric="offline_minutes",
                    unit="分钟",
                    trigger_value=30,
                    recover_value=5,
                )
            )
        for station in db.scalars(
            select(Station).where(Station.source == "demo", Station.status == "operating")
        ):
            key = f"demo-energy-{station.id}"
            if not db.scalar(
                select(EnergyRecord.id).where(
                    EnergyRecord.source == "demo", EnergyRecord.external_id == key
                )
            ):
                db.add(
                    EnergyRecord(
                        station_id=station.id,
                        source="demo",
                        external_id=key,
                        day=date(2026, 11, 1),
                        kwh=Decimal("120.05"),
                    )
                )


if __name__ == "__main__":
    main()
    business_seed()
