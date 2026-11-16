import csv
import io
from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from app import time_slot_aggregate
from app.contracts import AggregateTimeSlotReport, MeterSourcePage
from app.dependencies import DB, Actor
from app.hourly_energy import aligned
from app.models import Audit
from app.reporting import SnapshotDB
from app.routers.statistics import csv_cell

router = APIRouter(prefix="/api/v1/orders/time-slots/aggregate", tags=["orders"])


def filters(
    start_at: Annotated[datetime, Query()],
    end_at: Annotated[datetime, Query()],
    source: str = Query(..., min_length=1, max_length=50),
    q: str = Query("", max_length=150),
    district: str = Query("", max_length=50),
    operator_id: int | None = Query(None, ge=1, le=2147483647),
    meter_scope: Literal["station"] = "station",
    equipment_external_id: Literal[""] = "",
):
    if (
        not source.strip()
        or not aligned(start_at)
        or not aligned(end_at)
        or start_at >= end_at
        or end_at - start_at > timedelta(days=366)
    ):
        raise HTTPException(422, "请明确来源及北京时间整点区间，结束晚于开始，最多366天")
    return {
        "start_at": start_at,
        "end_at": end_at,
        "source": source,
        "q": q,
        "district": district,
        "operator_id": operator_id,
    }


Filters = Annotated[dict, Depends(filters)]


@router.get("/sources", response_model=MeterSourcePage)
def sources(
    user: Actor,
    db: DB,
    q: str = Query("", max_length=50),
    district: str = Query("", max_length=50),
    operator_id: int | None = Query(None, ge=1, le=2147483647),
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    return time_slot_aggregate.source_options(db, user, q, district, operator_id, page, page_size)


@router.get("", response_model=AggregateTimeSlotReport)
def report(user: Actor, db: SnapshotDB, params: Filters):
    return time_slot_aggregate.report(db, user, params)


@router.get(
    "/export",
    response_class=Response,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}}}},
)
def export(user: Actor, db: SnapshotDB, params: Filters):
    report = time_slot_aggregate.report(db, user, params)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "来源",
            "当前授权场站数",
            "场站/企业检索",
            "区域",
            "企业编号筛选",
            "开始(含)",
            "结束(不含)",
            "演示标识",
            "有订单观测场站数",
            "有计量记录场站数",
            "时段(北京)",
            "查询场站小时格",
            "已观测订单开始数",
            "完成订单开始数",
            "观测开始数占比(%)",
            "已知站级小时电量(kWh)",
            "已知格",
            "明确未知格",
            "未返回格(本流)",
            "源完整性已认证",
        ]
    )
    rows = 0
    for stream in report["streams"]:
        for point in stream["slots"]:
            values = [
                report["source"],
                report["station_count"],
                report["q"],
                report["district"],
                report["operator_id"],
                report["start_at"],
                report["end_at"],
                stream["is_demo"],
                stream["stations_with_order_observations"],
                stream["stations_with_meter_records"],
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
            writer.writerow([csv_cell(value) for value in values])
            rows += 1
    db.add(
        Audit(
            user_id=user.id,
            action="orders.time_slots.aggregate.export",
            detail={"rows": rows, "source": params["source"]},
        )
    )
    db.commit()
    return Response(
        "\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="ronghub-multi-station-time-slots.csv"'
        },
    )
