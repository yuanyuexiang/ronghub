"""Authorized source-reported orders; no charging control, settlement or guessed telemetry."""

import csv
import io
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, select

from app.contracts import ChargingOrderPage
from app.dependencies import DB, Actor
from app.models import Audit, ChargingOrder
from app.orders import statement
from app.routers.statistics import csv_cell

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])
NOTICE = "源报订单，数据完整性尚未核实；按开始时间筛选。源报时长不由起止时间推算，消费额不代表支付或结算，不与日电量及补贴合并计算。"


def filters(
    q: str = Query("", max_length=150),
    station_id: int | None = Query(None, ge=1, le=2147483647),
    operator_id: int | None = Query(None, ge=1, le=2147483647),
    equipment_external_id: str = Query("", max_length=100),
    status: str = Query("", max_length=20),
    source: str = Query("", max_length=50),
    start_at: datetime | None = None,
    end_at: datetime | None = None,
):
    if status not in {"", "unknown", "charging", "completed", "interrupted", "cancelled"}:
        raise HTTPException(422, "不支持的订单状态")
    for stamp in [start_at, end_at]:
        if stamp is not None and stamp.utcoffset() is None:
            raise HTTPException(422, "订单查询时间必须包含时区")
    if start_at and end_at and (start_at >= end_at or end_at - start_at > timedelta(days=366)):
        raise HTTPException(422, "查询结束须晚于开始，单次区间最多366天")
    return {
        "q": q,
        "station_id": station_id,
        "operator_id": operator_id,
        "equipment_external_id": equipment_external_id,
        "status": status,
        "source": source,
        "start_at": start_at,
        "end_at": end_at,
    }


Filters = Annotated[dict, Depends(filters)]


def output(row):
    order, station_name, operator_name, district = row
    keys = [
        "id",
        "operator_id",
        "station_id",
        "source",
        "external_id",
        "equipment_external_id",
        "connector_external_id",
        "status",
        "duration_seconds",
        "currency",
        "version",
        "is_demo",
    ]
    result = {key: getattr(order, key) for key in keys}
    for key in ["started_at", "ended_at", "source_updated_at", "synced_at"]:
        value = getattr(order, key)
        result[key] = value.isoformat() if value is not None else None
    for key in ["kwh", "amount"]:
        value = getattr(order, key)
        result[key] = str(value) if value is not None else None
    return {
        **result,
        "station_name": station_name,
        "operator_name": operator_name,
        "district": district,
    }


@router.get("", response_model=ChargingOrderPage)
def listing(
    user: Actor,
    db: DB,
    params: Filters,
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    stmt = statement(user, params)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(
        stmt.order_by(ChargingOrder.started_at.desc(), ChargingOrder.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [output(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "source_completeness_verified": False,
        "notice": NOTICE,
    }


@router.get(
    "/export",
    response_class=Response,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}}}},
)
def export(user: Actor, db: DB, params: Filters):
    # Limit+1 proves truncation before creating a file; no silently partial export.
    rows = list(
        db.execute(
            statement(user, params)
            .order_by(ChargingOrder.started_at.desc(), ChargingOrder.id.desc())
            .limit(10001)
        )
    )
    if len(rows) > 10000:
        raise HTTPException(422, "单次最多导出10000条订单，请缩小查询范围")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    keys = [
        "id",
        "source",
        "external_id",
        "station_name",
        "operator_name",
        "equipment_external_id",
        "connector_external_id",
        "status",
        "started_at",
        "ended_at",
        "duration_seconds",
        "kwh",
        "amount",
        "currency",
        "source_updated_at",
        "is_demo",
    ]
    writer.writerow(
        [
            "平台编号",
            "来源",
            "源订单号",
            "场站",
            "运营商",
            "源桩编码",
            "源枪编码",
            "状态",
            "开始时间(带时区)",
            "结束时间(带时区)",
            "源报充电时长(秒)",
            "源报电量(kWh)",
            "源报消费额",
            "币种",
            "源更新时间",
            "演示数据",
        ]
    )
    for row in rows:
        record = output(row)
        writer.writerow([csv_cell(record[key]) for key in keys])
    db.add(Audit(user_id=user.id, action="orders.export", detail={"rows": len(rows)}))
    db.commit()
    return Response(
        "\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="ronghub-orders.csv"'},
    )
