import csv
import io
from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from app import order_time_slots
from app.contracts import OrderTimeSlotReport
from app.dependencies import DB, Actor
from app.hourly_energy import aligned
from app.models import Audit
from app.routers.statistics import csv_cell

router = APIRouter(prefix="/api/v1/orders/time-slots", tags=["orders"])


def filters(
    start_at: Annotated[datetime, Query()],
    end_at: Annotated[datetime, Query()],
    station_id: int = Query(..., ge=1, le=2147483647),
    source: str = Query(..., min_length=1, max_length=50),
    meter_scope: Literal["station", "equipment"] = "station",
    equipment_external_id: str = Query("", max_length=100),
):
    if (
        not aligned(start_at)
        or not aligned(end_at)
        or start_at >= end_at
        or end_at - start_at > timedelta(days=366)
    ):
        raise HTTPException(422, "时段查询须带时区、对齐北京时间整点且结束晚于开始，最多366天")
    if (
        not source.strip()
        or (meter_scope == "station" and equipment_external_id)
        or (meter_scope == "equipment" and not equipment_external_id.strip())
    ):
        raise HTTPException(422, "请明确来源及计量层级，桩级查询须指定唯一源桩编码")
    return {
        "station_id": station_id,
        "source": source,
        "start_at": start_at,
        "end_at": end_at,
        "meter_scope": meter_scope,
        "equipment_external_id": equipment_external_id,
    }


Filters = Annotated[dict, Depends(filters)]


@router.get("", response_model=OrderTimeSlotReport)
def report(user: Actor, db: DB, params: Filters):
    return order_time_slots.report(db, user, params)


@router.get(
    "/export",
    response_class=Response,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}}}},
)
def export(user: Actor, db: DB, params: Filters):
    report = order_time_slots.report(db, user, params)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "场站",
            "来源",
            "计量层级",
            "源桩编码",
            "开始(含)",
            "结束(不含)",
            "演示标识",
            "时段(北京)",
            "查询小时槽位",
            "已观测订单开始数",
            "已完成订单开始数",
            "观测开始数占比(%)",
            "已知源报小时电量合计(kWh)",
            "已知槽位",
            "明确未知槽位",
            "未返回槽位",
            "源完整性已认证",
        ]
    )
    rows = 0
    for stream in report["streams"]:
        for point in stream["slots"]:
            writer.writerow(
                [
                    csv_cell(value)
                    for value in [
                        report["station_name"],
                        report["source"],
                        report["meter_scope"],
                        report["equipment_external_id"],
                        report["start_at"],
                        report["end_at"],
                        stream["is_demo"],
                        point["label"],
                        point["selected_slots"],
                        point["observed_order_starts"],
                        point["completed_order_starts"],
                        point["order_start_share_pct"],
                        point["kwh"],
                        point["known_meter_slots"],
                        point["unknown_meter_slots"],
                        point["unreported_meter_slots"],
                        False,
                    ]
                ]
            )
            rows += 1
    db.add(Audit(user_id=user.id, action="orders.time_slots.export", detail={"rows": rows}))
    db.commit()
    return Response(
        "\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="ronghub-order-time-slots.csv"'},
    )
