import csv
import io
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, select

from app import order_summary
from app.contracts import OrderSummaryPage
from app.dependencies import DB, Actor
from app.models import Audit, now
from app.routers.orders import Filters
from app.routers.statistics import csv_cell

router = APIRouter(prefix="/api/v1/orders/summary", tags=["orders"])
Dimension = Literal["station", "equipment"]
Granularity = Literal["day", "month", "year"]


def require_window(params):
    if not params["start_at"] or not params["end_at"]:
        raise HTTPException(422, "订单汇总须明确开始和结束时间，单次最多366天")


@router.get("", response_model=OrderSummaryPage)
def report(
    user: Actor,
    db: DB,
    params: Filters,
    dimension: Dimension = "station",
    granularity: Granularity = "day",
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    require_window(params)
    stmt = order_summary.grouped(user, params, dimension, granularity)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(order_summary.ordered(stmt).offset((page - 1) * page_size).limit(page_size))
    return {
        "items": [order_summary.output(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "dimension": dimension,
        "granularity": granularity,
        "window_start_at": params["start_at"].isoformat(),
        "window_end_at": params["end_at"].isoformat(),
        "generated_at": now().isoformat(),
        "source_completeness_verified": False,
        "notice": order_summary.NOTICE,
    }


@router.get(
    "/export",
    response_class=Response,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}}}},
)
def export(
    user: Actor,
    db: DB,
    params: Filters,
    dimension: Dimension = "station",
    granularity: Granularity = "day",
):
    require_window(params)
    rows = list(
        db.execute(
            order_summary.ordered(
                order_summary.grouped(user, params, dimension, granularity)
            ).limit(10001)
        )
    )
    if len(rows) > 10000:
        raise HTTPException(422, "单次最多导出10000个汇总分组，请缩小查询范围")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "周期起始日(北京)",
            "汇总维度",
            "周期粒度",
            "场站",
            "运营商",
            "区域",
            "来源",
            "源桩编码",
            "演示数据",
            "观测订单数",
            "已完成数",
            "充电中数",
            "中断数",
            "取消数",
            "未知状态数",
            "已知电量合计(kWh)",
            "已知电量完成订单数",
            "缺电量完成订单数",
            "已知源报时长合计(秒)",
            "已知时长完成订单数",
            "缺时长完成订单数",
            "已知消费额合计(CNY)",
            "已知消费额完成订单数",
            "缺消费额完成订单数",
            "查询开始(含)",
            "查询结束(不含)",
            "数据完整性已认证",
        ]
    )
    for row in rows:
        item = order_summary.output(row)
        values = [
            item["period_start"],
            dimension,
            granularity,
            item["station_name"],
            item["operator_name"],
            item["district"],
            item["source"],
            item["equipment_external_id"],
            item["is_demo"],
            item["observed_orders"],
            item["completed_orders"],
            item["charging_orders"],
            item["interrupted_orders"],
            item["cancelled_orders"],
            item["unknown_orders"],
        ]
        for metric in ["kwh", "duration_seconds", "amount"]:
            values.extend(
                [
                    item[metric]["value"],
                    item[metric]["known_orders"],
                    item[metric]["missing_orders"],
                ]
            )
        values.extend([params["start_at"].isoformat(), params["end_at"].isoformat(), False])
        writer.writerow([csv_cell(value) for value in values])
    db.add(
        Audit(
            user_id=user.id,
            action="orders.summary.export",
            detail={"rows": len(rows), "period": granularity},
        )
    )
    db.commit()
    return Response(
        "\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="ronghub-order-summary.csv"'},
    )
