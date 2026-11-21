"""Read finalized subsidy records without treating application submission as generation/payment."""

import csv
import io
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select

from app.authorization import require
from app.dependencies import SnapshotActor
from app.models import Audit, SubsidyAction, SubsidyClaim
from app.reporting import SnapshotDB
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


def ledger_data(db, user, q, status, paid_from, paid_to, page, page_size):
    require(db, user, "subsidy.read")
    if paid_to == date.max or (paid_from and paid_to and paid_from > paid_to):
        raise HTTPException(422, "日期范围无效")
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
        "items": [
            {
                "id": c.id,
                "station_id": c.station_id,
                "station_name": name,
                "operator_name": operator,
                "start_date": c.start_date.isoformat(),
                "end_date": c.end_date.isoformat(),
                "status": c.status,
                "base_amount": str(c.base_amount),
                "adjusted_amount": str(c.adjusted_amount),
                "generated_at": None,
                "approved_at": approved_at.isoformat() if approved_at else None,
                "recorded_disbursement_at": paid_at.isoformat() if paid_at else None,
            }
            for c, name, operator, station_status, submitted_at, approved_at, paid_at in rows
        ],
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
