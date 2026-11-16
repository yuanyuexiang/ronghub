"""Source faults under statistics capabilities and station identity scope."""

import csv
import io
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, select

from app import faults
from app.contracts import FaultEventPage, FaultSummary
from app.dependencies import DB, Actor
from app.hourly_energy import BEIJING
from app.models import Audit, FaultEvent
from app.routers.statistics import csv_cell

router = APIRouter(prefix="/api/v1/statistics/faults", tags=["statistics"])


def filters(
    q: str = Query("", max_length=150),
    station_id: int | None = Query(None, ge=1, le=2147483647),
    operator_id: int | None = Query(None, ge=1, le=2147483647),
    source: str = Query("", max_length=50),
    equipment_external_id: str = Query("", max_length=100),
    connector_external_id: str = Query("", max_length=100),
    status: str = Query("", max_length=20),
    severity: str = Query("", max_length=20),
    start_at: datetime | None = None,
    end_at: datetime | None = None,
):
    if status not in {"", "unknown", "active", "recovered"} or severity not in {
        "",
        "unknown",
        "info",
        "warning",
        "critical",
    }:
        raise HTTPException(422, "不支持的源故障状态或等级")
    if any(stamp is not None and stamp.utcoffset() is None for stamp in [start_at, end_at]):
        raise HTTPException(422, "故障查询时间必须带时区")
    if start_at and end_at and (start_at >= end_at or end_at - start_at > timedelta(days=366)):
        raise HTTPException(422, "故障发生时间查询最多366天且结束须晚于开始")
    return {
        "q": q,
        "station_id": station_id,
        "operator_id": operator_id,
        "source": source,
        "equipment_external_id": equipment_external_id,
        "connector_external_id": connector_external_id,
        "status": status,
        "severity": severity,
        "start_at": start_at,
        "end_at": end_at,
    }


Filters = Annotated[dict, Depends(filters)]


def output(row):
    event, station_name, operator_name, district = row
    result = {
        key: getattr(event, key)
        for key in [
            "id",
            "station_id",
            "operator_id",
            "source",
            "external_id",
            "equipment_external_id",
            "connector_external_id",
            "fault_code",
            "status",
            "severity",
            "version",
            "is_demo",
        ]
    }
    for key in ["started_at", "recovered_at", "source_updated_at", "synced_at"]:
        value = getattr(event, key)
        result[key] = value.isoformat() if value is not None else None
    # Exact conversion, avoiding float total_seconds loss on long source events.
    elapsed = event.recovered_at - event.started_at if event.recovered_at else None
    result["recovery_elapsed_seconds"] = (
        str(
            Decimal(elapsed.days * 86400 + elapsed.seconds)
            + Decimal(elapsed.microseconds) / 1000000
        )
        if elapsed is not None
        else None
    )
    return {
        **result,
        "station_name": station_name,
        "operator_name": operator_name,
        "district": district,
    }


@router.get("", response_model=FaultEventPage)
def listing(
    user: Actor,
    db: DB,
    params: Filters,
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    stmt = faults.statement(user, params)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(
        stmt.order_by(FaultEvent.started_at.desc(), FaultEvent.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [output(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "source_completeness_verified": False,
        "notice": faults.NOTICE,
    }


@router.get("/summary", response_model=FaultSummary)
def summary(user: Actor, db: DB, params: Filters):
    records = faults.statement(user, params).subquery()
    elapsed = func.extract("epoch", records.c.recovered_at - records.c.started_at)
    rows = list(
        db.execute(
            select(
                records.c.source,
                records.c.is_demo,
                func.count().label("observed"),
                func.count().filter(records.c.status == "active").label("active"),
                func.count().filter(records.c.status == "recovered").label("recovered"),
                func.count().filter(records.c.status == "unknown").label("unknown"),
                func.avg(elapsed).filter(records.c.status == "recovered").label("average"),
            )
            .group_by(records.c.source, records.c.is_demo)
            .order_by(records.c.source, records.c.is_demo)
            .limit(1001)
        )
    )
    if len(rows) > 1000:
        raise HTTPException(422, "故障汇总来源分组超过1000，请缩小范围")
    return {
        "streams": [
            {
                "source": row.source,
                "is_demo": row.is_demo,
                "observed_events": row.observed,
                "active_events": row.active,
                "recovered_events": row.recovered,
                "unknown_events": row.unknown,
                "average_recovery_elapsed_seconds": str(row.average)
                if row.average is not None
                else None,
            }
            for row in rows
        ],
        "source_completeness_verified": False,
        "notice": faults.NOTICE,
    }


@router.get(
    "/export",
    response_class=Response,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}}}},
)
def export(user: Actor, db: DB, params: Filters):
    start, end = params["start_at"], params["end_at"]
    if start is None or end is None:
        raise HTTPException(422, "故障导出须明确发生时间范围，最多跨2个北京时间自然月")
    first, last = start.astimezone(BEIJING), (end.astimezone(BEIJING) - timedelta(microseconds=1))
    if (last.year - first.year) * 12 + last.month - first.month > 1:
        raise HTTPException(422, "故障单次导出最多跨2个北京时间自然月")
    rows = list(
        db.execute(
            faults.statement(user, params)
            .order_by(FaultEvent.started_at.desc(), FaultEvent.id.desc())
            .limit(10001)
        )
    )
    if len(rows) > 10000:
        raise HTTPException(422, "单次最多导出10000条故障，请缩小范围")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    keys = [
        "id",
        "source",
        "external_id",
        "station_name",
        "operator_name",
        "district",
        "equipment_external_id",
        "connector_external_id",
        "fault_code",
        "status",
        "severity",
        "started_at",
        "recovered_at",
        "recovery_elapsed_seconds",
        "source_updated_at",
        "is_demo",
    ]
    writer.writerow(
        [
            "平台编号",
            "来源",
            "源事件号",
            "场站",
            "运营商",
            "区域",
            "源桩编码",
            "源枪编码",
            "源故障码",
            "源状态",
            "源等级",
            "发生时间(带时区)",
            "恢复时间(带时区)",
            "源报恢复经过秒数(非维修工时)",
            "源更新时间",
            "演示标识",
        ]
    )
    for row in rows:
        record = output(row)
        writer.writerow([csv_cell(record[key]) for key in keys])
    db.add(Audit(user_id=user.id, action="statistics.faults.export", detail={"rows": len(rows)}))
    db.commit()
    return Response(
        "\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="ronghub-fault-events.csv"'},
    )
