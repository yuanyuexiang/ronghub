"""Regional/operator quality observations with explicit cohort and denominator boundaries."""

import csv
import io
from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from app import quality, runtime
from app.contracts import MeterSourcePage, QualityReport
from app.dependencies import Actor
from app.models import Audit
from app.reporting import SnapshotDB
from app.routers.statistics import csv_cell

router = APIRouter(prefix="/api/v1/statistics/quality", tags=["statistics"])


def filters(
    start_at: Annotated[datetime, Query()],
    end_at: Annotated[datetime, Query()],
    source: str = Query(..., min_length=1, max_length=50),
    group_by: Literal["district", "operator", "station"] = "district",
    asset_scope: Literal["equipment", "connector"] = "connector",
    q: str = Query("", max_length=150),
    district: str = Query("", max_length=50),
    operator_id: int | None = Query(None, ge=1, le=2147483647),
):
    if (
        not source.strip()
        or any(stamp.utcoffset() is None for stamp in [start_at, end_at])
        or start_at >= end_at
        or end_at - start_at > timedelta(days=366)
    ):
        raise HTTPException(422, "请明确来源及带时区时间窗，结束晚于开始且最多366天")
    return {
        "start_at": start_at,
        "end_at": end_at,
        "source": source,
        "group_by": group_by,
        "asset_scope": asset_scope,
        "q": q,
        "district": district,
        "operator_id": operator_id,
    }


Filters = Annotated[dict, Depends(filters)]


@router.get("/sources", response_model=MeterSourcePage)
def sources(
    user: Actor,
    db: SnapshotDB,
    q: str = Query("", max_length=50),
    district: str = Query("", max_length=50),
    operator_id: int | None = Query(None, ge=1, le=2147483647),
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    return quality.sources(db, user, q, district, operator_id, page, page_size)


@router.get("", response_model=QualityReport)
def report(
    user: Actor,
    db: SnapshotDB,
    params: Filters,
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    return quality.report(db, user, params, page, page_size)


def export_columns():
    columns = [
        ("group_by", "分组方式"),
        ("group_key", "分组编号"),
        ("group_label", "当前分组名称"),
        ("source", "来源"),
        ("asset_scope", "运行区间层级"),
        ("q", "站企检索词"),
        ("district", "区域筛选"),
        ("operator_id", "企业筛选ID"),
        ("start_at", "开始(含)"),
        ("end_at", "结束(不含)"),
        ("is_demo", "演示标识"),
        ("selected_station_count", "当前查询台账站数"),
        ("station_count", "本组当前台账站数"),
        ("stations_with_runtime", "本组有运行区间的站数"),
        ("observed_object_count", "本组观测源对象数(非设备总数)"),
        ("observed_object_window_seconds", "观测对象乘查询秒数(非可用时长)"),
    ]
    state_labels = {
        "online": "在线",
        "offline": "离线",
        "unknown": "明确未知",
        "available": "空闲",
        "occupied": "占用",
        "charging": "充电",
        "faulted": "故障",
        "maintenance": "维护",
    }
    for dimension, states in runtime.STATES.items():
        prefix = "连接" if dimension == "connectivity" else "业务"
        for field, label in [
            ("reported_seconds", "已上送秒数"),
            ("known_seconds", "已知秒数"),
            ("unknown_seconds", "明确未知秒数"),
            ("unreported_seconds", "观测集合内未上送查询秒数"),
        ]:
            columns.append((f"{dimension}.{field}", prefix + label))
        for state in states:
            columns.extend(
                [
                    (f"{dimension}.{state}.seconds", f"{prefix}{state_labels[state]}秒数"),
                    (f"{dimension}.{state}.share", f"{prefix}{state_labels[state]}占本轴已上送(%)"),
                ]
            )
    columns.extend(
        [
            ("faults.observed_events", "源报故障事件数(不限层级、窗口内发生)"),
            ("faults.stations_with_events", "有源报故障的站数"),
            ("faults.active_events", "未恢复事件数"),
            ("faults.recovered_events", "已恢复事件数"),
            ("faults.unknown_events", "源状态未知事件数"),
            ("faults.average_recovery_elapsed_seconds", "已恢复样本平均经过秒数(非维修工时)"),
            ("source_completeness_verified", "源完整性已认证"),
        ]
    )
    return columns


@router.get(
    "/export",
    response_class=Response,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}}}},
)
def export(user: Actor, db: SnapshotDB, params: Filters):
    report = quality.report(db, user, params, 1, 10000, exporting=True)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    columns = export_columns()
    writer.writerow([label for _, label in columns])
    rows = 0
    for group in report["items"]:
        for stream in group["streams"] or [None]:
            record = {
                **report,
                "group_key": group["key"],
                "group_label": group["label"],
                "station_count": group["station_count"],
            }
            if stream:
                record.update(stream)
                for axis in stream["axes"]:
                    dimension = axis["dimension"]
                    record.update(
                        {
                            f"{dimension}.{field}": axis[field]
                            for field in [
                                "reported_seconds",
                                "known_seconds",
                                "unknown_seconds",
                                "unreported_seconds",
                            ]
                        }
                    )
                    for state in axis["states"]:
                        record[f"{dimension}.{state['state']}.seconds"] = state["observed_seconds"]
                        record[f"{dimension}.{state['state']}.share"] = state[
                            "share_of_reported_pct"
                        ]
                record.update(
                    {f"faults.{field}": value for field, value in stream["faults"].items()}
                )
            writer.writerow([csv_cell(record.get(key)) for key, _ in columns])
            rows += 1
    db.add(Audit(user_id=user.id, action="statistics.quality.export", detail={"rows": rows}))
    db.commit()
    return Response(
        "\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="ronghub-quality-observations.csv"'},
    )
