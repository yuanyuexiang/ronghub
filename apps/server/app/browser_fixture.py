"""Local-only browser test fixture, never used as production business initialization."""

import argparse
import json
import secrets
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select

from app.config import settings
from app.db import SessionLocal
from app.models import (
    AlertCursor,
    AlertEvent,
    AlertNotice,
    ApprovalAction,
    Article,
    ArticleAction,
    ArticleAttachment,
    ArticleReply,
    ArticleReplyAttachment,
    Attachment,
    Audit,
    ChargingOrder,
    Connector,
    EnergyAllocation,
    EnergyRecord,
    Equipment,
    FaultEvent,
    HourlyEnergyRecord,
    InspectionAction,
    InspectionAttachment,
    InspectionTask,
    RatingAction,
    RatingApplication,
    RatingAttachment,
    RatingStandard,
    RuntimeInterval,
    Station,
    StationApplication,
    SubsidyAction,
    SubsidyAnnualBatch,
    SubsidyAttachment,
    SubsidyClaim,
    SubsidyDeviceAllocation,
    SubsidyDeviceDay,
    SubsidyDeviceLine,
    SubsidyDeviceScope,
    SubsidyPolicy,
    User,
)


def main():
    if not settings.demo_mode:
        raise SystemExit("Browser fixtures require demo mode")
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["create", "clean", "prepare"])
    p.add_argument("--station", type=int)
    p.add_argument("--hourly-energy", action="store_true")
    p.add_argument("--runtime-history", type=int, default=0, choices=range(151))
    p.add_argument("--fault-history", type=int, default=0, choices=range(151))
    p.add_argument("--order-equipment-groups", action="store_true")
    p.add_argument("--order-history", type=int, default=0, choices=range(151))
    p.add_argument("--group-name", default="")
    p.add_argument("--count", type=int, default=1, choices=range(1, 31))
    p.add_argument("--operating", action="store_true")
    p.add_argument("--located", action="store_true")
    p.add_argument("--application-history", type=int, default=0, choices=range(151))
    p.add_argument("--business-dates", action="store_true")
    p.add_argument("--business-history", type=int, default=0, choices=range(151))
    p.add_argument("--subsidy-pending", type=int, default=0, choices=range(1, 102))
    p.add_argument("--inspection-history", type=int, default=0, choices=range(151))
    p.add_argument("--alert-history", type=int, default=0, choices=range(151))
    args = p.parse_args()
    if len(args.group_name) > 60:
        raise SystemExit("Fixture group name is too long")
    if args.alert_history and args.count != 1:
        raise SystemExit("Alert history requires one fixture station")
    if args.inspection_history and args.count != 1:
        raise SystemExit("Inspection history requires one fixture station")
    if args.subsidy_pending:
        from sqlalchemy.engine import make_url

        if make_url(settings.database_url).host not in {"localhost", "127.0.0.1"}:
            raise SystemExit("Pending subsidy fixtures require a local demo database")
    if args.subsidy_pending and (args.count != 1 or not args.operating or args.business_history):
        raise SystemExit(
            "Subsidy pending requires one operating fixture station without business history"
        )
    if args.business_dates and args.business_history != 30:
        raise SystemExit("Business dates require exactly 30 history records")
    if args.business_history and (args.count != 1 or not args.operating):
        raise SystemExit("Business history requires one operating fixture station")
    if args.application_history and (args.count != 1 or args.operating):
        raise SystemExit("Application history requires a single pending fixture station")
    with SessionLocal.begin() as db:
        if args.action == "prepare":
            from sqlalchemy.engine import make_url

            from app.dependencies import token_hash
            from app.models import LoginThrottle

            if make_url(settings.database_url).host not in {"localhost", "127.0.0.1"}:
                raise SystemExit("Browser preparation requires a local demo database")
            db.execute(
                delete(LoginThrottle).where(
                    LoginThrottle.key.in_(
                        [
                            token_hash(key)
                            for key in (
                                "ip:127.0.0.1",
                                "captcha:issue:127.0.0.1",
                                "captcha:verify:127.0.0.1",
                                "sms:bind-issue-ip:127.0.0.1",
                                "sms:bind-confirm-ip:127.0.0.1",
                                "sms:public-issue-ip:127.0.0.1",
                                "sms:public-confirm-ip:127.0.0.1",
                                "sms:staff-apply-ip:127.0.0.1",
                            )
                        ]
                    )
                )
            )
            return
        if args.action == "create":
            user = db.scalar(select(User).where(User.username == "operator_a"))
            if not user:
                raise SystemExit("Seed local demo users first")
            fixtures = []
            for _ in range(args.count):
                key = secrets.token_hex(8)
                station = Station(
                    operator_id=user.operator_id,
                    source="browser-test",
                    external_id=key,
                    name="浏览器验收-" + (args.group_name + "-" if args.group_name else "") + key,
                    district="鼓楼区",
                    address="临时演示站点",
                    power_kw=60,
                    connectors=1,
                    status="operating" if args.operating else "pending",
                    longitude="119.300000" if args.located else None,
                    latitude="26.080000" if args.located else None,
                    coordinate_system="wgs84" if args.located else "unknown",
                    location_is_demo=args.located,
                    station_type="charging",
                    usage_type="public" if args.located else "unknown",
                    construction_site="building_parking" if args.located else "unknown",
                    site_state="normal" if args.located else "unknown",
                    source_sync_state="active" if args.located else "unknown",
                )
                db.add(station)
                db.flush()
                if args.located:
                    equipment = Equipment(
                        station_id=station.id, external_id=key, name="地图演示设备", power_kw=60
                    )
                    db.add(equipment)
                    db.flush()
                    db.add(
                        Connector(equipment_id=equipment.id, external_id=key, status="available")
                    )
                if args.operating:
                    db.add(
                        EnergyRecord(
                            station_id=station.id,
                            source="browser-test",
                            external_id=key,
                            day=date(2026, 11, 1),
                            kwh="42.005",
                            eligible=True,
                        )
                    )
                fixture = {"station_id": station.id, "name": station.name}
                if args.runtime_history:
                    from app.runtime import ingest as ingest_runtime

                    start = datetime(2026, 11, 1, tzinfo=UTC)
                    samples = [
                        {
                            "external_id": f"{key}-runtime-{index:03}",
                            "equipment_external_id": "fixture-equipment",
                            "connector_external_id": "fixture-connector",
                            "dimension": "connectivity",
                            "state": "online"
                            if index % 3 == 0
                            else "offline"
                            if index % 3 == 1
                            else "unknown",
                            "started_at": start + timedelta(minutes=index),
                            "ended_at": start + timedelta(minutes=index + 1),
                            "source_updated_at": start + timedelta(days=1),
                        }
                        for index in range(args.runtime_history)
                    ]
                    samples.append(
                        {
                            "external_id": f"{key}-operation",
                            "equipment_external_id": "fixture-equipment",
                            "connector_external_id": "fixture-connector",
                            "dimension": "operation",
                            "state": "faulted",
                            "started_at": start,
                            "ended_at": start + timedelta(minutes=1),
                            "source_updated_at": start + timedelta(days=1),
                        }
                    )
                    # Separate equipment-level history must not be added to connector durations.
                    samples.append(
                        {
                            "external_id": f"{key}-equipment",
                            "equipment_external_id": "fixture-equipment",
                            "connector_external_id": "",
                            "dimension": "connectivity",
                            "state": "online",
                            "started_at": start,
                            "ended_at": start + timedelta(hours=1),
                            "source_updated_at": start + timedelta(days=1),
                        }
                    )
                    ingest_runtime(
                        db, station.source, station.operator_id, station, samples, is_demo=True
                    )
                if args.fault_history:
                    from app.faults import ingest as ingest_faults

                    start = datetime(2026, 11, 1, tzinfo=UTC)
                    samples = [
                        {
                            "external_id": f"{key}-fault-{index:03}",
                            "equipment_external_id": "fixture-equipment",
                            "connector_external_id": "fixture-connector",
                            "fault_code": "DEMO-FAULT",
                            "status": "recovered"
                            if index % 3 == 0
                            else "active"
                            if index % 3 == 1
                            else "unknown",
                            "severity": "warning",
                            "started_at": start + timedelta(minutes=index),
                            "recovered_at": start + timedelta(minutes=index, seconds=30)
                            if index % 3 == 0
                            else None,
                            "source_updated_at": start + timedelta(days=1),
                        }
                        for index in range(args.fault_history)
                    ]
                    ingest_faults(
                        db, station.source, station.operator_id, station, samples, is_demo=True
                    )
                if args.hourly_energy:
                    from app.hourly_energy import ingest as ingest_hourly

                    start = datetime(2026, 11, 1, tzinfo=UTC)
                    samples = [
                        {
                            "meter_scope": "station",
                            "started_at": start + timedelta(hours=index),
                            "ended_at": start + timedelta(hours=index + 1),
                            "kwh": value,
                            "source_updated_at": start + timedelta(hours=4),
                        }
                        for index, value in enumerate(["0.300000", None, "0.000000"])
                    ]
                    ingest_hourly(
                        db, station.source, station.operator_id, station, samples, is_demo=True
                    )
                if args.order_history:
                    from app.orders import ingest as ingest_orders

                    start = datetime(2026, 11, 1, tzinfo=UTC)
                    records = [
                        {
                            "external_id": f"browser-order-{key}-{index}",
                            "equipment_external_id": f"browser-equipment-{index:03d}"
                            if args.order_equipment_groups
                            else "browser-equipment",
                            "connector_external_id": "browser-connector",
                            "status": "completed",
                            "started_at": start + timedelta(minutes=index),
                            "ended_at": start + timedelta(minutes=index + 60),
                            "duration_seconds": 3300 if index else None,
                            "kwh": "10.500000" if index else None,
                            "amount": "12.34" if index else None,
                            "currency": "CNY" if index else None,
                            "source_updated_at": start + timedelta(minutes=index + 65),
                        }
                        for index in range(args.order_history)
                    ]
                    ingest_orders(
                        db, station.source, station.operator_id, station, records, is_demo=True
                    )
                    fixture["order_count"] = args.order_history
                if args.application_history:
                    from app.workflows import act, create

                    application_ids = []
                    for index in range(args.application_history):
                        application = create(
                            db,
                            user,
                            station.id,
                            "access",
                            {
                                "note": {"legacy": True} if index == 0 else "演示历史申请",
                            },
                        )
                        act(
                            db,
                            user,
                            application.id,
                            "withdraw",
                            application.version,
                            "演示历史撤回",
                        )
                        application_ids.append(application.id)
                    fixture["application_ids"] = application_ids
                if args.business_history:
                    from app import rating, subsidy

                    policy = db.scalar(
                        select(SubsidyPolicy).where(
                            SubsidyPolicy.code == "DEMO-KWH", SubsidyPolicy.version == 1
                        )
                    )
                    standard = db.scalar(
                        select(RatingStandard).where(
                            RatingStandard.code == "DEMO-RATING", RatingStandard.version == 1
                        )
                    )
                    if not policy or not standard:
                        raise SystemExit(
                            "Seed demo policy and standard before creating business history"
                        )
                    claims, ratings = [], []
                    for history_index in range(args.business_history):
                        claim = subsidy.create_claim(
                            db, user, station.id, policy.id, date(2026, 11, 1), date(2026, 11, 30)
                        )
                        subsidy.act(db, user, claim.id, "withdraw", claim.version, "演示历史撤回")
                        application = rating.create(
                            db, user, station.id, standard.id, {}, "演示历史"
                        )
                        rating.act(
                            db,
                            user,
                            application.id,
                            "withdraw",
                            application.version,
                            "演示历史撤回",
                            {},
                        )
                        if args.business_dates and history_index < 28:
                            start = datetime(2026, 11, 14, 16, tzinfo=UTC)
                            stamp = (
                                start - timedelta(microseconds=1)
                                if history_index == 0
                                else start + timedelta(days=1)
                                if history_index == 26
                                else start + timedelta(minutes=history_index)
                            )
                            for action_model, key_name, record_id in [
                                (SubsidyAction, "claim_id", claim.id),
                                (RatingAction, "application_id", application.id),
                            ]:
                                db.add(
                                    action_model(
                                        **{key_name: record_id},
                                        actor_id=user.id,
                                        action="submit",
                                        comment="Synthetic browser date receipt",
                                        created_at=stamp,
                                    )
                                )
                                if history_index == 27:
                                    db.add(
                                        action_model(
                                            **{key_name: record_id},
                                            actor_id=user.id,
                                            action="submit",
                                            comment="Synthetic later resubmission",
                                            created_at=start + timedelta(days=1, hours=1),
                                        )
                                    )
                        claims.append(claim.id)
                        ratings.append(application.id)
                    fixture["claim_ids"] = claims
                    fixture["rating_ids"] = ratings
                if args.subsidy_pending:
                    from app import subsidy

                    policy = db.scalar(
                        select(SubsidyPolicy).where(
                            SubsidyPolicy.code == "DEMO-KWH", SubsidyPolicy.version == 1
                        )
                    )
                    if not policy:
                        raise SystemExit("Seed demo subsidy policy first")
                    claim_ids = []
                    for index in range(args.subsidy_pending):
                        day = date(2026, 1, 1) + timedelta(days=index)
                        db.add(
                            EnergyRecord(
                                station_id=station.id,
                                source="browser-test",
                                external_id=f"{key}-batch-{index}",
                                day=day,
                                kwh="10.050000",
                                eligible=True,
                            )
                        )
                        db.flush()
                        claim = subsidy.create_claim(db, user, station.id, policy.id, day, day)
                        from app.subsidy_fixture import seed as seed_subsidy_evidence

                        seed_subsidy_evidence(db, claim, user)
                        subsidy.act(db, user, claim.id, "submit", 1, "")
                        claim_ids.append(claim.id)
                    fixture["claim_ids"] = claim_ids
                if args.inspection_history:
                    from app import inspection
                    from app.models import now

                    regulator = db.scalar(select(User).where(User.username == "regulator"))
                    if not regulator:
                        raise SystemExit("Seed demo regulator first")
                    task_ids = []
                    for index in range(args.inspection_history):
                        task = inspection.create(
                            db,
                            regulator,
                            station.id,
                            station.name + "-巡检历史",
                            "演示历史督办",
                            "major",
                            now() + timedelta(days=1),
                        )
                        task.created_at = now() - timedelta(days=2)
                        task.due_at = now() - timedelta(days=1)
                        task_ids.append(task.id)
                    fixture["task_ids"] = task_ids
                if args.alert_history:
                    from app import alerts
                    from app.models import AlertRule, now

                    regulator = db.scalar(select(User).where(User.username == "regulator"))
                    rule = db.scalar(
                        select(AlertRule).where(
                            AlertRule.code == "DEMO-OFFLINE", AlertRule.version == 1
                        )
                    )
                    if not regulator or not rule:
                        raise SystemExit("Seed demo regulator and alert rule first")
                    event_ids = []
                    for index in range(args.alert_history):
                        result = alerts.observe(
                            db,
                            regulator,
                            station.id,
                            rule.id,
                            "history-" + key + "-" + str(index),
                            rule.trigger_value + 1,
                            now() - timedelta(days=1) + timedelta(seconds=index),
                        )
                        event_ids.append(result["event_id"])
                    fixture["event_ids"] = event_ids
                fixtures.append(fixture)
            print(
                json.dumps(
                    fixtures[0] if args.count == 1 else {"items": fixtures}, ensure_ascii=False
                )
            )
        else:
            station = db.get(Station, args.station)
            if not station or station.source != "browser-test":
                raise SystemExit("Refusing to delete non-fixture station")
            fixture_station_ids = set(
                db.scalars(select(Station.id).where(Station.source == "browser-test"))
            )
            for batch in db.scalars(select(SubsidyAnnualBatch)):
                items = batch.receipt.get("items", [])
                if (
                    items
                    and any(item.get("station_id") == station.id for item in items)
                    and all(
                        isinstance(item, dict) and item.get("station_id") in fixture_station_ids
                        for item in items
                    )
                ):
                    db.execute(
                        delete(Audit).where(
                            Audit.action == "subsidy.annual_create", Audit.object_id == batch.id
                        )
                    )
                    db.delete(batch)
            # Remove only this verified fixture station's newly recorded transitions.
            db.execute(
                delete(Audit).where(
                    Audit.detail["after"]["station_id"].as_integer() == station.id,
                    Audit.detail["object_type"]
                    .as_string()
                    .in_(
                        [
                            "station_application",
                            "subsidy_claim",
                            "rating_application",
                            "inspection_task",
                            "alert_event",
                        ]
                    ),
                )
            )
            aids = list(
                db.scalars(
                    select(StationApplication.id).where(StationApplication.station_id == station.id)
                )
            )
            for a in db.scalars(select(Attachment).where(Attachment.application_id.in_(aids))):
                (Path(settings.storage_path).resolve() / a.storage_key).unlink(missing_ok=True)
                db.delete(a)
            db.flush()
            db.execute(delete(ApprovalAction).where(ApprovalAction.application_id.in_(aids)))
            db.execute(delete(StationApplication).where(StationApplication.id.in_(aids)))
            cids = select(SubsidyClaim.id).where(SubsidyClaim.station_id == station.id)
            for material in db.scalars(
                select(SubsidyAttachment).where(SubsidyAttachment.claim_id.in_(cids))
            ):
                (Path(settings.storage_path).resolve() / material.storage_key).unlink(
                    missing_ok=True
                )
                db.delete(material)
            db.flush()
            fixture_claim_ids = set(db.scalars(cids))
            if fixture_claim_ids:
                for summary in db.scalars(
                    select(Audit).where(Audit.action == "subsidy.batch_review")
                ):
                    rows = summary.detail.get("claims", [])
                    if rows and all(
                        isinstance(row, dict)
                        and type(row.get("id")) is int
                        and row["id"] in fixture_claim_ids
                        for row in rows
                    ):
                        db.delete(summary)

            db.execute(delete(EnergyAllocation).where(EnergyAllocation.claim_id.in_(cids)))
            db.execute(delete(SubsidyAction).where(SubsidyAction.claim_id.in_(cids)))
            db.execute(
                delete(SubsidyDeviceAllocation).where(
                    SubsidyDeviceAllocation.claim_id.in_(fixture_claim_ids)
                )
            )
            db.execute(
                delete(SubsidyDeviceLine).where(SubsidyDeviceLine.claim_id.in_(fixture_claim_ids))
            )
            db.execute(delete(SubsidyClaim).where(SubsidyClaim.station_id == station.id))
            rids = select(RatingApplication.id).where(RatingApplication.station_id == station.id)
            for material in db.scalars(
                select(RatingAttachment).where(RatingAttachment.application_id.in_(rids))
            ):
                (Path(settings.storage_path).resolve() / material.storage_key).unlink(
                    missing_ok=True
                )
                db.delete(material)
            db.flush()
            db.execute(
                delete(Audit).where(
                    Audit.action == "ratings.report.download", Audit.object_id.in_(rids)
                )
            )
            db.execute(delete(RatingAction).where(RatingAction.application_id.in_(rids)))
            db.execute(delete(RatingApplication).where(RatingApplication.station_id == station.id))
            db.execute(delete(EnergyRecord).where(EnergyRecord.station_id == station.id))
            tids = select(InspectionTask.id).where(InspectionTask.station_id == station.id)
            for attachment in db.scalars(
                select(InspectionAttachment).where(InspectionAttachment.task_id.in_(tids))
            ):
                (Path(settings.storage_path).resolve() / attachment.storage_key).unlink(
                    missing_ok=True
                )
                db.delete(attachment)
            db.flush()
            db.execute(delete(InspectionAction).where(InspectionAction.task_id.in_(tids)))
            db.execute(delete(InspectionTask).where(InspectionTask.station_id == station.id))
            events = select(AlertEvent.id).where(AlertEvent.station_id == station.id)
            db.execute(delete(AlertCursor).where(AlertCursor.station_id == station.id))
            db.execute(delete(AlertNotice).where(AlertNotice.event_id.in_(events)))
            db.execute(delete(AlertEvent).where(AlertEvent.station_id == station.id))
            articles = select(Article.id).where(
                Article.title == station.name, Article.is_demo.is_(True)
            )
            db.execute(
                delete(Audit).where(
                    Audit.detail["object_type"].as_string() == "article",
                    Audit.object_id.in_(articles),
                )
            )
            reply_ids = select(ArticleReply.id).where(ArticleReply.article_id.in_(articles))
            for image in db.scalars(
                select(ArticleReplyAttachment).where(ArticleReplyAttachment.reply_id.in_(reply_ids))
            ):
                (Path(settings.storage_path).resolve() / image.storage_key).unlink(missing_ok=True)
                db.delete(image)
            db.flush()
            db.execute(delete(ArticleReply).where(ArticleReply.article_id.in_(articles)))
            db.execute(delete(ArticleAction).where(ArticleAction.article_id.in_(articles)))
            for asset in db.scalars(
                select(ArticleAttachment).where(ArticleAttachment.article_id.in_(articles))
            ):
                (Path(settings.storage_path) / asset.storage_key).unlink(missing_ok=True)
            db.execute(delete(ArticleAttachment).where(ArticleAttachment.article_id.in_(articles)))
            db.execute(delete(Article).where(Article.id.in_(articles)))
            equipment_ids = select(Equipment.id).where(Equipment.station_id == station.id)
            db.execute(delete(Connector).where(Connector.equipment_id.in_(equipment_ids)))
            db.execute(delete(SubsidyDeviceDay).where(SubsidyDeviceDay.station_id == station.id))
            db.execute(
                delete(SubsidyDeviceScope).where(SubsidyDeviceScope.station_id == station.id)
            )
            db.execute(delete(Equipment).where(Equipment.station_id == station.id))
            db.execute(
                delete(HourlyEnergyRecord).where(HourlyEnergyRecord.station_id == station.id)
            )
            db.execute(delete(RuntimeInterval).where(RuntimeInterval.station_id == station.id))
            db.execute(delete(FaultEvent).where(FaultEvent.station_id == station.id))
            db.execute(delete(ChargingOrder).where(ChargingOrder.station_id == station.id))
            db.delete(station)


if __name__ == "__main__":
    main()
