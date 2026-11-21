"""Owned, repeatable customer-presentation dataset. Never enables production writes."""

import argparse
import hashlib
import io
import json
import re
import zipfile
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import UploadFile
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from app import (
    alerts,
    application_evidence,
    content,
    hazards,
    inspection,
    rating,
    subsidy,
    workflows,
)
from app.authorization import require
from app.config import settings
from app.db import SessionLocal
from app.inspection_documents import ExecutionContact
from app.models import (
    AlertEvent,
    AlertRule,
    Article,
    Audit,
    ChargingOrder,
    Connector,
    EnergyRecord,
    Equipment,
    FaultEvent,
    HazardCase,
    HourlyEnergyRecord,
    InspectionAttachment,
    InspectionTask,
    Operator,
    RatingApplication,
    RatingAttachment,
    RatingStandard,
    RuntimeInterval,
    Station,
    StationApplication,
    SubsidyAttachment,
    SubsidyClaim,
    SubsidyPolicy,
    User,
    now,
)
from app.storage import store_upload

SOURCE = "ronghub-showcase"
TZ = ZoneInfo("Asia/Shanghai")
DISTRICTS = [
    ("鼓楼区", "119.2965", "26.0845"),
    ("台江区", "119.3150", "26.0550"),
    ("仓山区", "119.3100", "26.0250"),
    ("晋安区", "119.3500", "26.0950"),
    ("马尾区", "119.4500", "26.0050"),
    ("长乐区", "119.5200", "25.9650"),
]
SITES = ["清和商务广场", "锦庭生活中心", "望江创智园", "云栖公共停车场"]
PUBLICATIONS = [
    (
        "充电站接入资料准备指南",
        "整理场站名称、地址、供电资料和联系人信息，核对设备及终端清单后发起接入申请。资料发生变更时，应重新核对后提交。",
    ),
    (
        "场站信息填报与更新说明",
        "场站基本资料、设备资料和接入进展可分别查询。建议定期检查场站名称、运营时段及设备清单，确保各项信息保持一致。",
    ),
    (
        "运营数据查询功能说明",
        "平台支持按日期、区域和运营商查询充电量，并提供独立来源的订单、小时计量和状态观测。缺少观测的日期不按零补齐。",
    ),
    (
        "场站安全巡检办理指引",
        "收到巡检任务后，先核对任务要求与完成期限，再填写执行信息并提交证明材料。监管复核完成后，任务状态相应更新。",
    ),
    (
        "星级评价申报材料整理建议",
        "按检测报告、站点平面信息、服务记录、安全管理记录和现场资料分类整理材料。申报提交后可查询评定进度与办理记录。",
    ),
    (
        "补贴申请资料整理说明",
        "核对场站、政策版本和电量区间后创建申请，补充申请说明和资料包，再提交审核。金额以对应核算依据及审核记录为准。",
    ),
    (
        "平台查询与导出使用指南",
        "先确认查询条件再执行导出。列表、翻页和导出均使用已应用的条件，未提交的搜索文字不会自动成为筛选依据。",
    ),
    (
        "运营商通知与反馈功能说明",
        "平台将通知按企业分别记录，运营商可查看通知内容并提交反馈。站内记录与外部短信发送状态分别展示。",
    ),
    (
        "充电设备信息维护要点",
        "设备编码、设备类型、额定功率与终端编码应保持清晰对应。运行状态与业务审批状态分别记录，便于按场站核查。",
    ),
    (
        "场站资料版本与办理记录说明",
        "提交审核时保留当次资料版本。后续变更不会覆盖已提交的材料，历史核查和办理记录可按事项追溯。",
    ),
    (
        "如何查看场站服务评价结果",
        "完成身份认证后，进入所属工作台查询场站评价结果、公示有效期及相关申报记录。入口选择不会改变账号权限。",
    ),
    (
        "场站日常安全管理清单",
        "日常关注终端完好情况、消防通道、设备标识和运营场所管理。发现问题后可通过巡检与隐患督办流程跟踪处理。",
    ),
    (
        "平台使用与联系说明",
        "账号与权限问题请联系所属单位的平台管理员；资料办理进度可在对应工作台查询。运营事项可通过企业通知记录提交反馈。",
    ),
]


def pdf_bytes(label):
    """A valid, visibly non-certifying reference PDF, without forged seals or reports."""
    message = f"Scenario reference: {label}. No certification or legal validity."
    stream = f"BT /F1 14 Tf 50 760 Td ({message}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    result = b"%PDF-1.4\n"
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    position = len(result)
    result += b"xref\n0 6\n0000000000 65535 f \n"
    result += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    return result + f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{position}\n%%EOF\n".encode()


def material(payload, filename, paths, archive=False):
    upload = UploadFile(filename=filename, file=io.BytesIO(payload))
    try:
        values, path = store_upload(
            upload,
            allow_archives=archive,
            allowed_types={"application/zip"} if archive else {"application/pdf"},
        )
    finally:
        upload.file.close()
    paths.append(path)
    return values


def measured_kwh(day, station_index, hour):
    base = Decimal(18 + (day.toordinal() + station_index * 7 + hour * 3) % 25) + Decimal("0.25")
    seasonal = Decimal(90 + day.month * 2) / 100
    weekday = Decimal("0.8") if day.weekday() >= 5 else Decimal(1)
    return (base * seasonal * weekday).quantize(Decimal("0.01"))


def put_rows(db, model, rows, *, update_energy=False, today=None):
    for start in range(0, len(rows), 400):
        statement = insert(model).values(rows[start : start + 400])
        if update_energy:
            statement = statement.on_conflict_do_update(
                index_elements=["source", "external_id"],
                set_={"kwh": statement.excluded.kwh},
                where=EnergyRecord.day == today,
            )
        else:
            keys = (
                [
                    "source",
                    "operator_id",
                    "station_id",
                    "meter_scope",
                    "equipment_external_id",
                    "started_at",
                ]
                if model is HourlyEnergyRecord
                else ["source", "operator_id", "external_id"]
            )
            statement = statement.on_conflict_do_nothing(index_elements=keys)
        db.execute(statement)


def seed(db, *, namespace=SOURCE, publish_portal=False, actor_names=None, paths=None, day=None):
    if not settings.demo_mode:
        raise ValueError("Presentation seed requires DEMO_MODE=true")
    if not re.fullmatch(r"ronghub-showcase(?:-[a-z0-9]{1,12})?", namespace):
        raise ValueError("Invalid presentation namespace")
    paths = paths if paths is not None else []
    stamp = now()
    local = stamp.astimezone(TZ)
    today = day or local.date()
    if today != local.date():
        raise ValueError("Presentation observations must use the current Beijing day")
    lock_key = (
        int.from_bytes(hashlib.sha256(namespace.encode()).digest()[:8], "big") & 0x7FFFFFFFFFFFFFFF
    )
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    names = actor_names or {
        "regulator": "regulator",
        "association": "association",
        "operator_a": "operator_a",
        "operator_b": "operator_b",
    }
    actors = {
        key: db.scalar(select(User).where(User.username == name)) for key, name in names.items()
    }
    if any(not u or not u.enabled or u.allowed_districts is not None for u in actors.values()):
        raise ValueError(
            "Enabled unrestricted base accounts are required; no permissions are changed"
        )
    if (
        actors["regulator"].role != "regulator"
        or actors["association"].role != "association"
        or any(
            actors[k].role != "operator" or not actors[k].operator_id
            for k in ["operator_a", "operator_b"]
        )
    ):
        raise ValueError("Base account roles do not match")
    if actors["operator_a"].operator_id == actors["operator_b"].operator_id:
        raise ValueError("Two distinct operators are required")
    reg, assoc = actors["regulator"], actors["association"]
    for capability in [
        "applications.review",
        "content.create",
        "content.publish",
        "inspections.assign",
        "hazards.create",
        "subsidy.review",
    ]:
        require(db, reg, capability)
    if publish_portal:
        require(db, reg, "content.public")
    for capability in ["ratings.review", "ratings.publish"]:
        require(db, assoc, capability)
    for key in ["operator_a", "operator_b"]:
        for capability in [
            "applications.create",
            "applications.submit",
            "ratings.create",
            "ratings.submit",
            "subsidy.create",
            "subsidy.submit",
        ]:
            require(db, actors[key], capability)
    if namespace == SOURCE:
        replacements = {
            "演示运营商甲": "榕辰新能源运营有限公司",
            "演示运营商乙": "江岚充电服务有限公司",
        }
        for key in ["operator_a", "operator_b"]:
            company = db.get(Operator, actors[key].operator_id, with_for_update=True)
            if company.name in replacements:
                company.name = replacements[company.name]
                company.version += 1
                # Empty profile fields are initialised only during the guarded rename.
                if not company.registration_address:
                    company.registration_address = "福州市清和园区运营服务中心"
                if not company.legal_representative:
                    company.legal_representative = "企业运营负责人"
                if not company.service_phone:
                    company.service_phone = "0591-00000000"
                if not company.contacts:
                    company.contacts = [
                        {
                            "name": "运营服务专员",
                            "phone": "0591-00000000",
                            "email": "service@example.invalid",
                            "position": "运营管理",
                            "primary": True,
                        }
                    ]
        for old, new in [
            ("演示·鼓楼公共充电站", "鼓楼清和公共充电站"),
            ("演示·仓山绿色充电站", "仓山望江公共充电站"),
            ("演示·晋安便民充电站", "晋安锦庭便民充电站"),
        ]:
            station = db.scalar(
                select(Station).where(Station.source == "demo", Station.name == old)
            )
            if station:
                station.name = new
    stations = []
    for index in range(24):
        owner = actors["operator_a" if index % 2 == 0 else "operator_b"]
        code = f"RH-{index + 1:03d}"
        station = db.scalar(
            select(Station).where(Station.source == namespace, Station.external_id == code)
        )
        if station and station.operator_id != owner.operator_id:
            raise ValueError("Owned station operator changed; refusing to overwrite")
        if station is None:
            district, longitude, latitude = DISTRICTS[index // 4]
            station = Station(
                source=namespace,
                external_id=code,
                operator_id=owner.operator_id,
                name=f"{district[:-1]}·{SITES[index % 4]}{'换电站' if index in [8, 17] else '充电站'}",
                district=district,
                address=f"{district}·{SITES[index % 4]}停车区",
                power_kw=Decimal(367),
                connectors=8,
                longitude=Decimal(longitude) + Decimal(index % 4) / 1000,
                latitude=Decimal(latitude) + Decimal(index % 4) / 1500,
                coordinate_system="wgs84",
                location_is_demo=True,
                station_type="swapping" if index in [8, 17] else "charging",
                usage_type="public",
                construction_site="building_parking",
                site_state="normal",
                source_sync_state="active",
                source_first_connected_at=stamp - timedelta(days=90),
                source_first_reported_at=stamp - timedelta(days=90),
                supply_type="direct",
                supply_capacity=Decimal(500),
                supply_capacity_unit="kVA",
                independent_connection=True,
                parking_spaces=16,
                business_hours="全天开放",
                station_contact_name="场站服务专员",
                station_contact_phone="0591-00000000",
                commissioned_on=today - timedelta(days=90) if index < 18 else None,
                status="operating"
                if index < 18
                else "accepted"
                if index < 20
                else "access_approved"
                if index < 22
                else "pending",
            )
            db.add(station)
            db.flush()
        devices = []
        for d in range(4):
            device = db.scalar(
                select(Equipment).where(
                    Equipment.station_id == station.id, Equipment.external_id == f"EVSE-{d + 1:02d}"
                )
            )
            if device is None:
                device = Equipment(
                    station_id=station.id,
                    external_id=f"EVSE-{d + 1:02d}",
                    name=f"{'直流快充' if d < 3 else '交流慢充'}设备 {d + 1:02d}",
                    power_kw=120 if d < 3 else 7,
                    equipment_type="dc" if d < 3 else "ac",
                    model_name="RH-DC120" if d < 3 else "RH-AC07",
                    source_unique_code=f"{code}-{d + 1:02d}",
                    manufactured_on=today - timedelta(days=180),
                )
                db.add(device)
                db.flush()
            devices.append(device)
            for c in range(2):
                if not db.scalar(
                    select(Connector.id).where(
                        Connector.equipment_id == device.id, Connector.external_id == f"C-{c + 1}"
                    )
                ):
                    db.add(
                        Connector(
                            equipment_id=device.id,
                            external_id=f"C-{c + 1}",
                            name=f"终端 {c + 1}",
                            interface_type="国标直流" if d < 3 else "国标交流",
                            status="offline"
                            if index < 18 and index % 4 == 0 and d == 0 and c == 0
                            else [
                                "available",
                                "charging",
                                "occupied",
                                "available",
                                "charging",
                                "available",
                                "offline",
                                "faulted",
                            ][(index + d * 2 + c) % 8],
                        )
                    )
        stations.append((station, owner, devices))
    db.flush()
    energy, orders, hours, runtime, faults = [], [], [], [], []
    for index, (station, owner, devices) in enumerate(stations[:18]):
        for offset in range(31):
            date = today - timedelta(days=offset)
            maximum = local.hour if offset == 0 else 24
            day_energy = Decimal(0)
            for hour in range(maximum):
                start = datetime.combine(date, time(hour), TZ)
                device = devices[1 + hour % 2] if index % 4 == 0 else devices[hour % 3]
                kwh = measured_kwh(date, index, hour)
                day_energy += kwh
                key = f"{index}-{date}-{hour}"
                orders.append(
                    {
                        "station_id": station.id,
                        "operator_id": owner.operator_id,
                        "source": namespace,
                        "external_id": key,
                        "equipment_external_id": device.external_id,
                        "connector_external_id": "C-1",
                        "status": "completed",
                        "started_at": start + timedelta(minutes=10),
                        "ended_at": start + timedelta(minutes=40),
                        "duration_seconds": 1800,
                        "kwh": kwh,
                        "amount": (kwh * Decimal("1.20")).quantize(Decimal("0.01")),
                        "currency": "CNY",
                        "source_updated_at": start + timedelta(minutes=45),
                        "is_demo": True,
                    }
                )
                hours.append(
                    {
                        "station_id": station.id,
                        "operator_id": owner.operator_id,
                        "source": namespace,
                        "meter_scope": "station",
                        "equipment_external_id": "",
                        "started_at": start,
                        "ended_at": start + timedelta(hours=1),
                        "kwh": kwh,
                        "source_updated_at": start + timedelta(hours=1),
                        "is_demo": True,
                    }
                )
                if offset < 2:
                    base = {
                        "station_id": station.id,
                        "operator_id": owner.operator_id,
                        "source": namespace,
                        "equipment_external_id": devices[0].external_id,
                        "connector_external_id": "C-1",
                        "is_demo": True,
                        "source_updated_at": start + timedelta(hours=1),
                    }
                    runtime.append(
                        {
                            **base,
                            "external_id": f"{key}-connection",
                            "dimension": "connectivity",
                            "state": "offline"
                            if index % 4 == 0 and (offset == 0 or hour >= 3)
                            else "online",
                            "started_at": start,
                            "ended_at": start + timedelta(hours=1),
                        }
                    )
                    for part, (begin, end) in enumerate([(0, 10), (10, 40), (40, 60)]):
                        runtime.append(
                            {
                                **base,
                                "external_id": f"{key}-operation-{part}",
                                "dimension": "operation",
                                "state": "charging" if part == 1 and hour % 3 == 0 else "available",
                                "started_at": start + timedelta(minutes=begin),
                                "ended_at": start + timedelta(minutes=end),
                            }
                        )
            if maximum:
                energy.append(
                    {
                        "station_id": station.id,
                        "source": namespace,
                        "external_id": f"{index}-{date}",
                        "day": date,
                        "kwh": day_energy,
                        "eligible": True,
                    }
                )
        for offset in range(31, 366):
            date = today - timedelta(days=offset)
            energy.append(
                {
                    "station_id": station.id,
                    "source": namespace,
                    "external_id": f"{index}-{date}",
                    "day": date,
                    "kwh": sum(measured_kwh(date, index, h) for h in range(24)),
                    "eligible": True,
                }
            )
        fault_start = datetime.combine(today - timedelta(days=1), time(3), TZ)
        faults.append(
            {
                "station_id": station.id,
                "operator_id": owner.operator_id,
                "source": namespace,
                "external_id": f"fault-{index}",
                "equipment_external_id": devices[0].external_id,
                "connector_external_id": "C-1",
                "fault_code": "COMMUNICATION_INTERRUPTED" if index % 2 else "CONNECTOR_TEMPERATURE",
                "status": "active" if index % 4 == 0 else "recovered",
                "severity": "warning",
                "started_at": fault_start,
                "recovered_at": None
                if index % 4 == 0
                else fault_start + timedelta(minutes=25 + index),
                "source_updated_at": stamp,
                "is_demo": True,
            }
        )
    put_rows(db, EnergyRecord, energy, update_energy=True, today=today)
    for model, rows in [
        (ChargingOrder, orders),
        (HourlyEnergyRecord, hours),
        (RuntimeInterval, runtime),
        (FaultEvent, faults),
    ]:
        put_rows(db, model, rows)
    db.flush()
    alert_code = "AL-" + namespace.upper()
    alert_rule = db.scalar(
        select(AlertRule).where(AlertRule.code == alert_code, AlertRule.version == 1)
    )
    if alert_rule is None:
        alert_rule = AlertRule(
            code=alert_code,
            version=1,
            name="终端离线监测",
            metric="offline_minutes",
            unit="分钟",
            trigger_value=30,
            recover_value=5,
            notifications_enabled=True,
            is_demo=True,
            created_by=reg.id,
            updated_by=reg.id,
            created_at=stamp,
            updated_at=stamp,
        )
        db.add(alert_rule)
        db.flush()
    for index, (station, owner, devices) in enumerate(stations[:18]):
        if db.scalar(
            select(AlertEvent.id).where(
                AlertEvent.rule_id == alert_rule.id, AlertEvent.station_id == station.id
            )
        ):
            continue
        started = datetime.combine(today - timedelta(days=1), time(3), TZ)
        alerts.observe(db, reg, station.id, alert_rule.id, "EVSE-01/C-1", Decimal(65), started)
        db.flush()
        if index % 4:
            alerts.observe(
                db,
                reg,
                station.id,
                alert_rule.id,
                "EVSE-01/C-1",
                Decimal(1),
                started + timedelta(minutes=25 + index),
            )
            db.flush()
    code = "SC-" + namespace.upper()
    policy = db.scalar(
        select(SubsidyPolicy).where(SubsidyPolicy.code == code, SubsidyPolicy.version == 1)
    )
    if policy is None:
        policy = SubsidyPolicy(
            code=code,
            version=1,
            name="运营电量补贴测算方案",
            rate=Decimal("0.10"),
            effective_from=today - timedelta(days=365),
            effective_to=today + timedelta(days=365),
            is_demo=True,
        )
        db.add(policy)
        db.flush()
    from app.subsidy_rules import DeviceSubsidyRules

    configuration_code = "CFG-" + namespace.upper()
    if not db.scalar(
        select(SubsidyPolicy.id).where(
            SubsidyPolicy.code == configuration_code, SubsidyPolicy.version == 1
        )
    ):
        config = DeviceSubsidyRules(
            configuration_version=1,
            swap_rate=Decimal("0.12"),
            generation_period="month",
            star_coefficients={str(i): Decimal("0.7") + Decimal(i) / 10 for i in range(6)},
            fault_rate_below=5,
            offline_rate_below=10,
        )
        db.add(
            SubsidyPolicy(
                code=configuration_code,
                version=1,
                name="充换电补贴规则配置",
                rate=Decimal("0.10"),
                effective_from=today - timedelta(days=365),
                effective_to=today + timedelta(days=365),
                is_demo=True,
                device_rules=config.model_dump(mode="json"),
            )
        )
        db.flush()
    standard = db.scalar(
        select(RatingStandard).where(RatingStandard.code == code, RatingStandard.version == 1)
    )
    scores = {"facility": 92, "safety": 94, "service": 90, "environment": 88}
    if standard is None:
        categories = [
            {"code": key, "name": name, "weight": str(weight)}
            for key, name, weight in [
                ("facility", "设施设备", 35),
                ("safety", "运维安全", 30),
                ("service", "服务体验", 25),
                ("environment", "环境配套", 10),
            ]
        ]
        standard = RatingStandard(
            code=code,
            version=1,
            name="场站服务质量评价指标",
            is_demo=True,
            rules={
                "categories": categories,
                "indicators": [
                    {
                        "code": c["code"],
                        "name": c["name"] + "综合评估",
                        "category": c["code"],
                        "weight": "1",
                        "max_score": "100",
                        "source": "manual",
                        "enabled": True,
                        "effective_from": (today - timedelta(days=365)).isoformat(),
                        "description": "按场景资料核对，采用人工复核评分",
                    }
                    for c in categories
                ],
                "thresholds": [
                    {"minimum": str(score), "stars": star}
                    for score, star in [(60, 1), (70, 2), (80, 3), (90, 4), (95, 5)]
                ],
            },
        )
        db.add(standard)
        db.flush()
    db.flush()
    # Read numeric/date defaults back in their persisted form before freezing evidence.
    db.expire_all()
    for index, (station, owner, devices) in enumerate(stations):
        if index >= 18:
            if not db.scalar(
                select(StationApplication.id).where(StationApplication.station_id == station.id)
            ):
                kind = "commission" if index < 20 else "acceptance" if index < 22 else "access"
                app = workflows.create(
                    db,
                    owner,
                    station.id,
                    kind,
                    {"contact": {"name": "场站经办员", "phone": "0591-00000000"}},
                )
                if index % 2 == 0:
                    token = application_evidence.context(db, owner, app).review_token
                    workflows.act(
                        db, owner, app.id, "submit", app.version, "提交场站资料核查", token
                    )
            continue
        if not db.scalar(
            select(RatingApplication.id).where(RatingApplication.station_id == station.id)
        ):
            values = {key: score - (index % 3) * 5 for key, score in scores.items()}
            app = rating.create(
                db, owner, station.id, standard.id, values, "场站服务资料已整理，请核对评审依据。"
            )
            for category in [
                "inspection_report",
                "site_plan",
                "service_record",
                "safety_record",
                "field_record",
            ]:
                filename = {
                    "inspection_report": "设施检查资料说明.pdf",
                    "site_plan": "场站平面信息.pdf",
                    "service_record": "运营服务记录.pdf",
                    "safety_record": "安全管理记录.pdf",
                    "field_record": "现场核查记录.pdf",
                }[category]
                file = material(pdf_bytes(category), filename, paths)
                db.add(
                    RatingAttachment(
                        application_id=app.id, uploaded_by=owner.id, category=category, **file
                    )
                )
            db.flush()
            if index % 6 != 5:
                rating.act(db, owner, app.id, "submit", app.version, "资料提交", {})
                if index % 6 == 4:
                    rating.act(db, assoc, app.id, "reject", app.version, "请补充现场资料说明", {})
                elif index % 6 != 3:
                    rating.act(
                        db, assoc, app.id, "review", app.version, "已完成本场景资料复核", values
                    )
                    if index % 6 < 2:
                        rating.act(
                            db,
                            assoc,
                            app.id,
                            "publish",
                            app.version,
                            "发布场站服务评价结果",
                            {},
                            today + timedelta(days=365),
                        )
        if not db.scalar(
            select(SubsidyClaim.id).where(
                SubsidyClaim.station_id == station.id, SubsidyClaim.policy_id == policy.id
            )
        ):
            claim = subsidy.create_claim(
                db,
                owner,
                station.id,
                policy.id,
                today - timedelta(days=7),
                today - timedelta(days=1),
                "按本次场站电量区间提交测算资料，金额以核算快照为准。",
            )
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as packet:
                packet.writestr(
                    "说明.txt", "本资料包为虚构场景材料，不是真实申报、财务或支付凭证。"
                )
                packet.writestr(
                    "station.csv", f"station,period\n{station.external_id},seven-complete-days\n"
                )
            db.add(
                SubsidyAttachment(
                    claim_id=claim.id,
                    uploaded_by=owner.id,
                    **material(archive.getvalue(), "运营电量与申报资料.zip", paths, True),
                )
            )
            db.flush()
            if index % 5:
                subsidy.act(db, owner, claim.id, "submit", claim.version, "提交电量资料")
                if index % 5 == 4:
                    subsidy.act(db, reg, claim.id, "reject", claim.version, "请补充区间资料说明")
                elif index % 5 >= 2:
                    subsidy.act(
                        db, reg, claim.id, "approve", claim.version, "已核对本场景电量测算快照"
                    )
                    if index % 5 == 3:
                        subsidy.act(
                            db,
                            reg,
                            claim.id,
                            "record_disbursement",
                            claim.version,
                            "场景发放登记，不执行资金支付",
                        )
        title = "设备与运营场所例行巡检"
        if not db.scalar(
            select(InspectionTask.id).where(
                InspectionTask.station_id == station.id, InspectionTask.title == title
            )
        ):
            task = inspection.create(
                db,
                reg,
                station.id,
                title,
                "核对设备标识、终端完好情况及场所安全管理，提交执行记录。",
                "normal",
                stamp + timedelta(days=7),
            )
            if index % 3:
                db.add(
                    InspectionAttachment(
                        task_id=task.id,
                        uploaded_by=owner.id,
                        **material(pdf_bytes("Inspection evidence"), "巡检执行记录.pdf", paths),
                    )
                )
                db.flush()
                inspection.act(
                    db,
                    owner,
                    task.id,
                    task.version,
                    "reply",
                    "已完成设备及场所检查，提交本次执行记录。",
                    contact=ExecutionContact(name="场站安全员", phone="0591-00000000"),
                    performed_at=task.created_at,
                )
                if index % 3 == 2:
                    inspection.act(
                        db, reg, task.id, task.version, "close", "已核对反馈记录，完成本项巡检。"
                    )
        if index < 6 and not db.scalar(
            select(HazardCase.id).where(HazardCase.station_id == station.id)
        ):
            hazard = hazards.create(
                db,
                reg,
                hazards.HazardCreateInput(
                    station_id=station.id,
                    operator_id=owner.operator_id,
                    title="场所标识与通道管理完善",
                    description="请复核停车区域引导标识和设备周边通道，完成后提交整改记录。",
                    severity="major" if index % 3 == 0 else "normal",
                    due_at=stamp + timedelta(days=5),
                ),
            )
            if index % 2:
                hazards.act(
                    db,
                    owner,
                    hazard.id,
                    hazards.HazardActionInput(
                        version=hazard.version, action="start", comment="已安排现场核查与整改。"
                    ),
                )
    # Stable content ownership is recorded in audit metadata rather than changing official data.
    for index, (title, body) in enumerate(PUBLICATIONS):
        identity = f"{namespace}:article:{index}"
        if db.scalar(
            select(Audit.id).where(
                Audit.action == "presentation.content", Audit.detail["key"].as_string() == identity
            )
        ):
            continue
        article = Article(
            kind="contact"
            if index == len(PUBLICATIONS) - 1
            else "announcement"
            if index % 3 == 0
            else "news",
            title=title,
            body=body
            + "\n\n办理过程中请核对页面提示及当前资料版本。如需补正，可通过对应事项查看办理记录。",
            audience="all",
            portal_public=publish_portal,
            created_by=reg.id,
            is_demo=True,
        )
        db.add(article)
        db.flush()
        content.record(db, reg, article, "create")
        if publish_portal:
            content.apply_action(
                db, reg, article, "publish", article.version, stamp - timedelta(days=index, hours=1)
            )
        db.add(
            Audit(
                user_id=reg.id,
                action="presentation.content",
                object_id=article.id,
                detail={"key": identity, "namespace": namespace, "synthetic": True},
            )
        )
    for key in ["operator_a", "operator_b"]:
        owner = actors[key]
        for index, title in enumerate(["请核对本月场站基础信息", "请完善场站服务及安全管理记录"]):
            identity = f"{namespace}:notice:{key}:{index}"
            if not db.scalar(
                select(Audit.id).where(
                    Audit.action == "presentation.content",
                    Audit.detail["key"].as_string() == identity,
                )
            ):
                article = Article(
                    kind="notice",
                    title=title,
                    body="请在工作台核对所属场站的基础资料与办理记录，并按实际情况提交反馈。",
                    audience="operator",
                    recipient_ids=[owner.operator_id],
                    portal_public=False,
                    created_by=reg.id,
                    is_demo=True,
                )
                db.add(article)
                db.flush()
                content.record(db, reg, article, "create")
                content.apply_action(
                    db, reg, article, "publish", article.version, stamp - timedelta(days=index + 1)
                )
                db.add(
                    Audit(
                        user_id=reg.id,
                        action="presentation.content",
                        object_id=article.id,
                        detail={"key": identity, "namespace": namespace, "synthetic": True},
                    )
                )
    db.flush()
    owned_stations = select(Station.id).where(Station.source == namespace)
    owned_articles = select(Audit.object_id).where(
        Audit.action == "presentation.content", Audit.detail["namespace"].as_string() == namespace
    )
    counts = {
        "stations": db.scalar(
            select(func.count()).select_from(Station).where(Station.source == namespace)
        ),
        "devices": db.scalar(
            select(func.count())
            .select_from(Equipment)
            .where(Equipment.station_id.in_(owned_stations))
        ),
        "connectors": db.scalar(
            select(func.count())
            .select_from(Connector)
            .where(
                Connector.equipment_id.in_(
                    select(Equipment.id).where(Equipment.station_id.in_(owned_stations))
                )
            )
        ),
        "publications": db.scalar(
            select(func.count())
            .select_from(Article)
            .where(
                Article.id.in_(owned_articles),
                Article.portal_public.is_(True),
                Article.status == "published",
            )
        ),
        "operating_stations": db.scalar(
            select(func.count())
            .select_from(Station)
            .where(Station.source == namespace, Station.status == "operating")
        ),
    }
    for model, label in [
        (EnergyRecord, "daily_energy"),
        (ChargingOrder, "orders"),
        (RuntimeInterval, "runtime_intervals"),
        (FaultEvent, "faults"),
        (HourlyEnergyRecord, "hourly_energy"),
    ]:
        counts[label] = db.scalar(
            select(func.count()).select_from(model).where(model.source == namespace)
        )
    for model, label in [
        (AlertEvent, "alerts"),
        (RatingApplication, "ratings"),
        (SubsidyClaim, "subsidies"),
        (InspectionTask, "inspections"),
        (HazardCase, "hazards"),
        (StationApplication, "station_applications"),
    ]:
        counts[label] = db.scalar(
            select(func.count()).select_from(model).where(model.station_id.in_(owned_stations))
        )
    result = {
        "namespace": namespace,
        **counts,
        "window_days": 366,
        "detailed_window_days": 31,
        "synthetic": True,
    }
    db.add(Audit(user_id=reg.id, action="presentation.seed", detail=result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--publish-portal",
        action="store_true",
        help="Explicitly publish only this dataset's new synthetic service guides",
    )
    args = parser.parse_args()
    if not settings.demo_mode:
        raise SystemExit("Presentation seed requires DEMO_MODE=true")
    if not args.apply:
        print(
            json.dumps(
                {
                    "namespace": SOURCE,
                    "planned_stations": 24,
                    "planned_devices": 96,
                    "planned_publications": len(PUBLICATIONS) if args.publish_portal else 0,
                    "apply": False,
                }
            )
        )
        return
    paths = []
    try:
        with SessionLocal.begin() as db:
            report = seed(db, publish_portal=args.publish_portal, paths=paths)
    except BaseException:
        for path in paths:
            path.unlink(missing_ok=True)
        raise
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
