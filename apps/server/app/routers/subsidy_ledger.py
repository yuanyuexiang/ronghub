"""Read finalized subsidy records without treating application submission as generation/payment."""

import csv
import io
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select

from app import orders, subsidy
from app.authorization import require
from app.contracts import ChargingOrderPage
from app.dependencies import SnapshotActor
from app.models import Audit, ChargingOrder, SubsidyAction, SubsidyClaim
from app.reporting import SnapshotDB
from app.routers import orders as order_routes
from app.routers.business_exports import CSV, LIMIT
from app.routers.business_exports import response as csv_response
from app.routers.statistics import csv_cell
from app.routers.subsidy import claims_statement

router = APIRouter(prefix="/api/v1/subsidy/ledger", tags=["subsidy"])


class LedgerItem(BaseModel):
    id: int
    station_id: int
    station_name: str
    operator_name: str
    start_date: str
    end_date: str
    status: str
    total_kwh: str
    application_year: int | None = None
    base_amount: str
    adjusted_amount: str
    generated_at: None = None
    approved_at: str | None
    recorded_disbursement_at: str | None


class LedgerPage(BaseModel):
    items: list[LedgerItem]
    total: int
    page: int
    page_size: int


BEIJING = ZoneInfo("Asia/Shanghai")
ORDER_NOTICE = "按该补贴记录的场站和北京时间核算周期查询源报订单，仅供核对。当前无补贴与订单的逐笔关联，订单电量不计入本记录的补贴核算依据。"


def ledger_statement(user, q="", status=""):
    approved = (
        select(func.min(SubsidyAction.created_at))
        .where(SubsidyAction.claim_id == SubsidyClaim.id, SubsidyAction.action == "approve")
        .correlate(SubsidyClaim)
        .scalar_subquery()
    )
    paid = (
        select(func.min(SubsidyAction.created_at))
        .where(
            SubsidyAction.claim_id == SubsidyClaim.id, SubsidyAction.action == "record_disbursement"
        )
        .correlate(SubsidyClaim)
        .scalar_subquery()
    )
    stmt = (
        claims_statement(user, q, "", None, None)
        .add_columns(approved.label("approved_at"), paid.label("recorded_disbursement_at"))
        .where(SubsidyClaim.status.in_([status] if status else ["approved", "disbursed"]))
    )
    return stmt, approved, paid


def ledger_item(row):
    claim, name, operator, _, _, approved_at, paid_at = row
    return {
        "id": claim.id,
        "station_id": claim.station_id,
        "station_name": name,
        "operator_name": operator,
        "start_date": claim.start_date.isoformat(),
        "end_date": claim.end_date.isoformat(),
        "status": claim.status,
        "total_kwh": str(claim.total_kwh),
        "application_year": (claim.snapshot or {}).get("application_year"),
        "base_amount": str(claim.base_amount),
        "adjusted_amount": str(claim.adjusted_amount),
        "generated_at": None,
        "approved_at": approved_at.isoformat() if approved_at else None,
        "recorded_disbursement_at": paid_at.isoformat() if paid_at else None,
    }


def ledger_data(db, user, q, status, paid_from, paid_to, page, page_size):
    require(db, user, "subsidy.read")
    if paid_to == date.max or (paid_from and paid_to and paid_from > paid_to):
        raise HTTPException(422, "日期范围无效")
    stmt, approved, paid = ledger_statement(user, q, status)
    if paid_from:
        stmt = stmt.where(paid >= datetime.fromisoformat(f"{paid_from}T00:00:00+08:00"))
    if paid_to:
        stmt = stmt.where(
            paid < datetime.fromisoformat(f"{paid_to}T00:00:00+08:00") + timedelta(days=1)
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(
        stmt.order_by(approved.asc().nulls_last(), SubsidyClaim.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [ledger_item(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("", response_model=LedgerPage)
def ledger(
    user: SnapshotActor,
    db: SnapshotDB,
    paid_from: date | None = None,
    paid_to: date | None = None,
    q: str = Query("", max_length=150),
    status: Literal["", "approved", "disbursed"] = "",
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    return ledger_data(db, user, q, status, paid_from, paid_to, page, page_size)


@router.get(
    "/export",
    response_class=Response,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}}}},
)
def export(
    user: SnapshotActor,
    db: SnapshotDB,
    paid_from: date | None = None,
    paid_to: date | None = None,
    q: str = Query("", max_length=150),
    status: Literal["", "approved", "disbursed"] = "",
):
    require(db, user, "subsidy.export")
    result = ledger_data(db, user, q, status, paid_from, paid_to, 1, 10001)
    if result["total"] > 10000:
        raise HTTPException(422, "导出超过10000条，请缩小范围；未截断导出")
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "记录编号",
            "场站",
            "运营商",
            "补贴生成时间",
            "核定时间",
            "申请周期开始",
            "申请周期结束",
            "汇算补贴金额",
            "核定补贴金额",
            "发放登记时间",
            "状态",
        ]
    )
    for item in result["items"]:
        writer.writerow(
            [
                csv_cell(v)
                for v in [
                    item["id"],
                    item["station_name"],
                    item["operator_name"],
                    "未留存",
                    item["approved_at"],
                    item["start_date"],
                    item["end_date"],
                    item["base_amount"],
                    item["adjusted_amount"],
                    item["recorded_disbursement_at"],
                    item["status"],
                ]
            ]
        )
    db.add(Audit(user_id=user.id, action="subsidy.ledger.export", detail={"rows": result["total"]}))
    db.commit()
    return Response(
        content=("\ufeff" + output.getvalue()).encode(),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="ronghub-subsidy-ledger.csv"',
            "Cache-Control": "private, no-store",
        },
    )


def finalized_claim(db, user, claim_id):
    require(db, user, "subsidy.read")
    claim = subsidy.claim_access(db, user, claim_id)
    if claim.status not in {"approved", "disbursed"}:
        raise HTTPException(404, "未找到已核定补贴记录")
    return claim


@router.get("/{claim_id}/export", response_class=Response, responses=CSV)
def export_detail(claim_id: int, user: SnapshotActor, db: SnapshotDB):
    require(db, user, "subsidy.export")
    finalized_claim(db, user, claim_id)
    stmt, _, _ = ledger_statement(user)
    item = ledger_item(db.execute(stmt.where(SubsidyClaim.id == claim_id)).one())
    return csv_response(
        db,
        user,
        "subsidy",
        "ledger-detail",
        [
            "记录编号",
            "申请年度",
            "场站",
            "运营商",
            "补贴周期开始",
            "补贴周期结束",
            "用于核算的电量(kWh)",
            "汇算补贴金额（元）",
            "核定金额（元）",
            "补贴生成时间",
            "核定时间",
            "发放登记时间",
        ],
        [
            [
                item["id"],
                item["application_year"],
                item["station_name"],
                item["operator_name"],
                item["start_date"],
                item["end_date"],
                item["total_kwh"],
                item["base_amount"],
                item["adjusted_amount"],
                item["generated_at"],
                item["approved_at"],
                item["recorded_disbursement_at"],
            ]
        ],
    )


def reference_order_params(db, user, claim_id):
    require(db, user, "orders.read")
    claim = finalized_claim(db, user, claim_id)
    return {
        "station_id": claim.station_id,
        "operator_id": claim.operator_id,
        "start_at": datetime.combine(claim.start_date, time.min, BEIJING),
        "end_at": (
            datetime.combine(claim.end_date + timedelta(days=1), time.min, BEIJING)
            if claim.end_date < date.max
            else None
        ),
        "q": "",
        "source": "",
        "status": "",
        "equipment_external_id": "",
    }


@router.get("/{claim_id}/orders", response_model=ChargingOrderPage)
def reference_orders(
    claim_id: int,
    user: SnapshotActor,
    db: SnapshotDB,
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    params = reference_order_params(db, user, claim_id)
    result = order_routes.listing(user, db, params, page=page, page_size=page_size)
    return {**result, "notice": ORDER_NOTICE}


@router.get("/{claim_id}/orders/export", response_class=Response, responses=CSV)
def export_reference_orders(claim_id: int, user: SnapshotActor, db: SnapshotDB):
    require(db, user, "orders.export")
    require(db, user, "subsidy.export")
    params = reference_order_params(db, user, claim_id)
    rows = list(
        db.execute(
            orders.statement(user, params)
            .order_by(ChargingOrder.started_at.desc(), ChargingOrder.id.desc())
            .limit(LIMIT + 1)
        )
    )
    return csv_response(
        db,
        user,
        "subsidy",
        "reference-orders",
        [
            "补贴记录编号",
            "口径",
            "来源",
            "订单号",
            "源报充电量(kWh)",
            "场站",
            "源桩编码",
            "终端编码",
            "状态",
            "开始时间(带时区)",
            "演示数据",
        ],
        [
            [
                claim_id,
                "同站同周期参考订单，未与补贴逐笔关联",
                order.source,
                order.external_id,
                order.kwh,
                station,
                order.equipment_external_id,
                order.connector_external_id,
                order.status,
                order.started_at.isoformat(),
                order.is_demo,
            ]
            for order, station, _, _ in rows
        ],
    )
