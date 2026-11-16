"""Scoped device observations for subsidy review; no public fact ingestion endpoint."""

import csv
import io
from datetime import date

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, select

from app.authorization import require
from app.dependencies import Actor
from app.device_day_contracts import SubsidyDeviceDayPage
from app.models import Audit, Equipment, Operator, Station, SubsidyDeviceDay
from app.reporting import SnapshotDB
from app.routers.statistics import csv_cell
from app.scopes import station_scope
from app.subsidy_device_days import NOTICE, ratio

router = APIRouter(prefix="/api/v1/subsidy/device-days", tags=["subsidy device evidence"])
EXPORT_LIMIT = 10000
CSV = {200: {"content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}}}}


def statement(user, start_date, end_date, station_id, equipment_external_id, source):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "仅监管与所属企业可查看补贴设备观测")
    if start_date > end_date or (end_date - start_date).days > 365:
        raise HTTPException(422, "设备日观测查询最多366天且结束不早于开始")
    d = SubsidyDeviceDay
    stmt = station_scope(
        select(d, Station.name, Operator.name, Station.district, Equipment.name)
        .join(Station, Station.id == d.station_id)
        .join(Operator, Operator.id == Station.operator_id)
        .join(Equipment, (Equipment.id == d.equipment_id) & (Equipment.station_id == Station.id))
        .where(
            d.operator_id == Station.operator_id,
            d.source == Station.source,
            d.day >= start_date,
            d.day <= end_date,
        ),
        user,
    )
    if station_id is not None:
        stmt = stmt.where(d.station_id == station_id)
    if equipment_external_id:
        stmt = stmt.where(d.equipment_external_id == equipment_external_id)
    if source:
        stmt = stmt.where(d.source == source)
    return stmt


def output(row):
    item, station, operator, district, current_name = row
    data = {
        key: getattr(item, key)
        for key in [
            "id",
            "station_id",
            "equipment_id",
            "operator_id",
            "source",
            "external_id",
            "equipment_external_id",
            "equipment_name",
            "equipment_type",
            "business_kind",
            "energy_complete",
            "metric_basis",
            "version",
            "is_demo",
        ]
    }
    for key in ["day", "production_date", "source_updated_at", "synced_at"]:
        value = getattr(item, key)
        data[key] = value.isoformat() if value is not None else None
    for key in [
        "power_kw",
        "energy_kwh",
        *[
            f"{axis}_{part}"
            for axis in ["fault", "offline"]
            for part in ["observed_seconds", "unknown_seconds", "seconds"]
        ],
    ]:
        value = getattr(item, key)
        data[key] = str(value) if value is not None else None
    for axis in ["fault", "offline"]:
        data[axis + "_percent"], data[axis + "_percent_reason"] = ratio(item, axis)
    data.update(
        station_name=station,
        operator_name=operator,
        district=district,
        current_equipment_name=current_name,
        mapping_basis="source_station_equipment_key",
        settlement_verified=False,
        observation_complete=item.energy_complete
        and item.business_kind != "unknown"
        and data["fault_percent"] is not None
        and data["offline_percent"] is not None,
    )
    return data


@router.get("", response_model=SubsidyDeviceDayPage)
def listing(
    user: Actor,
    db: SnapshotDB,
    start_date: date,
    end_date: date,
    station_id: int | None = Query(None, ge=1, le=2147483647),
    equipment_external_id: str = Query("", max_length=100),
    source: str = Query("", max_length=50),
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    stmt = statement(user, start_date, end_date, station_id, equipment_external_id, source)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(
        stmt.order_by(SubsidyDeviceDay.day.desc(), SubsidyDeviceDay.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [output(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "notice": NOTICE,
    }


@router.get("/export", response_class=Response, responses=CSV)
def export(
    user: Actor,
    db: SnapshotDB,
    start_date: date,
    end_date: date,
    station_id: int | None = Query(None, ge=1, le=2147483647),
    equipment_external_id: str = Query("", max_length=100),
    source: str = Query("", max_length=50),
):
    require(db, user, "subsidy.read")
    stmt = statement(user, start_date, end_date, station_id, equipment_external_id, source)
    rows = list(
        db.execute(
            stmt.order_by(SubsidyDeviceDay.day.desc(), SubsidyDeviceDay.id.desc()).limit(
                EXPORT_LIMIT + 1
            )
        )
    )
    if len(rows) > EXPORT_LIMIT:
        raise HTTPException(422, "单次最多导出10000条，请缩小范围；未截断导出")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "日期（北京时间）",
            "当前场站名称",
            "当前企业名称",
            "设备编码",
            "来源设备名称",
            "当前台账设备名称",
            "设备类型",
            "来源功率kW",
            "生产日期",
            "业务类型",
            "独立日计量kWh",
            "日计量完整",
            "故障上报秒数",
            "故障未知秒数",
            "故障秒数",
            "离线上报秒数",
            "离线未知秒数",
            "离线秒数",
            "故障日历日比例%",
            "故障比例缺失原因",
            "离线日历日比例%",
            "离线比例缺失原因",
            "来源",
            "来源记录",
            "修订",
            "源修订时间",
            "演示数据",
            "结算核验",
        ]
    )
    for row in rows:
        d = output(row)
        writer.writerow(
            [
                csv_cell(value)
                for value in [
                    d["day"],
                    d["station_name"],
                    d["operator_name"],
                    d["equipment_external_id"],
                    d["equipment_name"],
                    d["current_equipment_name"],
                    d["equipment_type"],
                    d["power_kw"],
                    d["production_date"],
                    d["business_kind"],
                    d["energy_kwh"],
                    "完整" if d["energy_complete"] else "未完整",
                    *[
                        d[f"{axis}_{part}"]
                        for axis in ["fault", "offline"]
                        for part in ["observed_seconds", "unknown_seconds", "seconds"]
                    ],
                    d["fault_percent"],
                    {
                        None: "—",
                        "missing": "未上报",
                        "basis_unconfirmed": "口径未确认",
                        "partial": "部分覆盖",
                        "unknown_states": "含未知状态",
                    }[d["fault_percent_reason"]],
                    d["offline_percent"],
                    {
                        None: "—",
                        "missing": "未上报",
                        "basis_unconfirmed": "口径未确认",
                        "partial": "部分覆盖",
                        "unknown_states": "含未知状态",
                    }[d["offline_percent_reason"]],
                    d["source"],
                    d["external_id"],
                    d["version"],
                    d["source_updated_at"],
                    "是" if d["is_demo"] else "否",
                    "未核验，不可结算",
                ]
            ]
        )
    db.add(Audit(user_id=user.id, action="subsidy.device_days.export", detail={"rows": len(rows)}))
    db.commit()
    return Response(
        "\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="ronghub-device-days.csv"',
            "Cache-Control": "no-store",
        },
    )
