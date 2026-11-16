"""Authorized historical state coverage, not certified operating availability."""

import csv
import io
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, or_, select

from app import runtime
from app.contracts import RuntimeObjectPage, RuntimeReport, RuntimeStationPage
from app.dependencies import Actor
from app.models import Audit, RuntimeInterval, Station
from app.reporting import SnapshotDB
from app.routers.statistics import csv_cell
from app.scopes import station_scope

router = APIRouter(prefix="/api/v1/statistics/runtime", tags=["statistics"])


def filters(
    start_at: Annotated[datetime, Query()],
    end_at: Annotated[datetime, Query()],
    station_id: int = Query(..., ge=1, le=2147483647),
    source: str = Query(..., min_length=1, max_length=50),
    equipment_external_id: str = Query(..., min_length=1, max_length=100),
    connector_external_id: str = Query("", max_length=100),
):
    if (
        any(stamp.utcoffset() is None for stamp in [start_at, end_at])
        or start_at >= end_at
        or end_at - start_at > timedelta(days=366)
    ):
        raise HTTPException(422, "运行状态窗口须带时区、结束晚于开始且最多366天")
    if (
        not source.strip()
        or not equipment_external_id.strip()
        or (connector_external_id and not connector_external_id.strip())
    ):
        raise HTTPException(422, "请明确来源及源桩编码；枪级须填写源枪编码")
    return {
        "station_id": station_id,
        "source": source,
        "equipment_external_id": equipment_external_id,
        "connector_external_id": connector_external_id,
        "start_at": start_at,
        "end_at": end_at,
    }


Filters = Annotated[dict, Depends(filters)]


@router.get("/stations", response_model=RuntimeStationPage)
def stations(
    user: Actor,
    db: SnapshotDB,
    q: str = Query("", max_length=150),
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "无权查看历史运行状态")
    stmt = station_scope(select(Station.id, Station.name, Station.source), user)
    if q.strip():
        stmt = stmt.where(Station.name.icontains(q.strip(), autoescape=True))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.order_by(Station.id).offset((page - 1) * page_size).limit(page_size))
    return {
        "items": [dict(row._mapping) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/objects", response_model=RuntimeObjectPage)
def objects(
    user: Actor,
    db: SnapshotDB,
    station_id: int = Query(..., ge=1, le=2147483647),
    source: str = Query(..., min_length=1, max_length=50),
    q: str = Query("", max_length=100),
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    if not source.strip():
        raise HTTPException(422, "请明确来源")
    station = runtime.station_for(db, user, {"station_id": station_id})
    stmt = select(
        RuntimeInterval.equipment_external_id, RuntimeInterval.connector_external_id
    ).where(
        RuntimeInterval.station_id == station.id,
        RuntimeInterval.operator_id == station.operator_id,
        RuntimeInterval.source == source,
    )
    if q.strip():
        stmt = stmt.where(
            or_(
                RuntimeInterval.equipment_external_id.icontains(q.strip(), autoescape=True),
                RuntimeInterval.connector_external_id.icontains(q.strip(), autoescape=True),
            )
        )
    stmt = stmt.distinct()
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(
        stmt.order_by(RuntimeInterval.equipment_external_id, RuntimeInterval.connector_external_id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [dict(row._mapping) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("", response_model=RuntimeReport)
def report(
    user: Actor,
    db: SnapshotDB,
    params: Filters,
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    station = runtime.station_for(db, user, params)
    return runtime.report(db, station, params, page, page_size)


@router.get(
    "/export",
    response_class=Response,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}}}},
)
def export(user: Actor, db: SnapshotDB, params: Filters):
    station = runtime.station_for(db, user, params)
    rows = list(
        db.scalars(
            runtime.statement(station, params)
            .order_by(RuntimeInterval.started_at, RuntimeInterval.id)
            .limit(10001)
        )
    )
    if len(rows) > 10000:
        raise HTTPException(422, "运行状态单次最多导出10000个区间，请缩小范围")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "场站",
            "来源",
            "源桩编码",
            "源枪编码",
            "查询开始(含)",
            "查询结束(不含)",
            "源区间号",
            "维度",
            "源状态",
            "源区间开始",
            "源区间结束",
            "窗口内开始",
            "窗口内结束",
            "窗口内观测秒数",
            "源更新时间",
            "演示标识",
            "源完整性已认证",
        ]
    )
    for row in rows:
        item = runtime.output(row, params)
        writer.writerow(
            [
                csv_cell(value)
                for value in [
                    station.name,
                    params["source"],
                    params["equipment_external_id"],
                    params["connector_external_id"],
                    params["start_at"].isoformat(),
                    params["end_at"].isoformat(),
                    row.external_id,
                    row.dimension,
                    row.state,
                    row.started_at.isoformat(),
                    row.ended_at.isoformat(),
                    item["clipped_start_at"],
                    item["clipped_end_at"],
                    item["observed_seconds"],
                    row.source_updated_at.isoformat(),
                    row.is_demo,
                    False,
                ]
            ]
        )
    db.add(Audit(user_id=user.id, action="statistics.runtime.export", detail={"rows": len(rows)}))
    db.commit()
    return Response(
        "\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="ronghub-runtime-intervals.csv"'},
    )
