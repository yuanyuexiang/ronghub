"""Bounded event CSV, sharing the list scope and exact filters."""

import csv
import io
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app import alerts
from app.alert_queries import Category
from app.alert_queries import query as event_query
from app.authorization import require
from app.dependencies import SnapshotActor
from app.models import AlertEvent, Audit
from app.reporting import SnapshotDB
from app.routers.statistics import csv_cell

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])
BEIJING = ZoneInfo("Asia/Shanghai")


def export_window(start_at, end_at):
    if start_at.utcoffset() is None or end_at.utcoffset() is None:
        raise HTTPException(422, "告警导出时间必须包含时区")
    try:
        start, end = start_at.astimezone(BEIJING), end_at.astimezone(BEIJING)
        month_index = start.year * 12 + start.month - 1 + 2
        limit = start.replace(
            year=month_index // 12,
            month=month_index % 12 + 1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    except (ValueError, OverflowError) as error:
        raise HTTPException(422, "告警导出日期超出支持范围") from error
    if end <= start or end > limit:
        raise HTTPException(422, "单次最多导出两个自然月，结束不含且须晚于开始（北京时间）")


@router.get(
    "/export",
    response_class=Response,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string"}}}}},
)
def export(
    user: SnapshotActor,
    db: SnapshotDB,
    start_at: datetime,
    end_at: datetime,
    q: str = Query("", max_length=150),
    status: str = Query("", max_length=30),
    acknowledged: bool | None = None,
    category: Category | None = None,
    rule_id: int | None = Query(None, ge=1, le=9223372036854775807),
    record_id: int | None = Query(None, ge=1, le=9223372036854775807),
):
    require(db, user, "alerts.read")
    export_window(start_at, end_at)
    stmt = event_query(
        user,
        q=q,
        status=status,
        acknowledged=acknowledged,
        category=category,
        rule_id=rule_id,
        record_id=record_id,
        start_at=start_at,
        end_at=end_at,
    )
    rows = db.execute(stmt.order_by(AlertEvent.id.desc()).limit(10001)).all()
    if len(rows) > 10000:
        raise HTTPException(422, "结果超过10000条，请缩小时间或筛选范围")
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "事件编号",
            "场站",
            "运营商",
            "对象",
            "规则代码",
            "规则版本",
            "指标",
            "发生时间",
            "最近观测时间",
            "恢复时间",
            "恢复状态",
            "确认时间",
            "确认说明",
            "最近完整比较值",
            "单位",
            "累计异常样本",
            "当前等级",
            "历史最高等级",
            "来源依据",
            "演示标识",
        ]
    )
    for event, station, operator in rows:
        value = alerts.output(event)
        evidence = event.configuration_evidence or {}
        latest = evidence.get("latest", {})
        writer.writerow(
            [
                csv_cell(item)
                for item in [
                    event.id,
                    station,
                    operator,
                    event.target,
                    event.rule_snapshot.get("code"),
                    event.rule_snapshot.get("version"),
                    event.rule_snapshot.get("metric"),
                    value["opened_at"],
                    value["last_observed_at"],
                    value["recovered_at"],
                    event.status,
                    value["acknowledged_at"],
                    event.acknowledge_note,
                    value["last_value"],
                    event.rule_snapshot.get("unit"),
                    event.occurrences,
                    evidence.get("current_level"),
                    evidence.get("highest_level"),
                    latest.get("source_basis", "legacy_unknown"),
                    event.is_demo,
                ]
            ]
        )
    content = "\ufeff" + buffer.getvalue()
    db.add(
        Audit(
            user_id=user.id,
            action="alerts.export",
            detail={
                "rows": len(rows),
                "start_at": start_at.isoformat(),
                "end_at": end_at.isoformat(),
                "category": category,
            },
        )
    )
    db.commit()
    return Response(
        content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="ronghub-alert-events.csv"',
        },
    )
