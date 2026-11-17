from datetime import date
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response

from app import charging_report, charging_report_excel
from app.authorization import require
from app.dependencies import SnapshotActor
from app.models import Audit, now
from app.reporting import SnapshotDB

router = APIRouter(prefix="/api/v1/statistics/charging-subsidy", tags=["charging subsidy report"])


def filters(
    period_date: date,
    period: charging_report.Period = "month",
    dimension: charging_report.Dimension = "station",
    q: str = Query("", max_length=150),
):
    return {"period_date": period_date, "period": period, "dimension": dimension, "q": q}


Filters = Annotated[dict, Depends(filters)]


def reads(db, user):
    for capability in ["statistics.read", "orders.read", "subsidy.read"]:
        require(db, user, capability)


@router.get("", response_model=charging_report.ChargingReport)
def report(
    user: SnapshotActor,
    db: SnapshotDB,
    params: Filters,
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    reads(db, user)
    return charging_report.collect(db, user, **params, page=page, page_size=page_size)


@router.get(
    "/export",
    response_class=Response,
    responses={
        200: {
            "content": {
                charging_report_excel.MEDIA: {"schema": {"type": "string", "format": "binary"}}
            }
        }
    },
)
def export(user: SnapshotActor, db: SnapshotDB, params: Filters):
    reads(db, user)
    require(db, user, "orders.export")
    report = charging_report.collect(db, user, **params, page=1, page_size=10000, exporting=True)
    payload = charging_report_excel.workbook(report)
    kind = "场站" if report.dimension == "station" else "电桩"
    filename = f"运营商{kind}补贴汇总-{now().astimezone(charging_report.BEIJING):%Y%m%d%H%M%S}.xlsx"
    result = Response(
        payload,
        media_type=charging_report_excel.MEDIA,
        headers={
            "Content-Disposition": f"attachment; filename=ronghub-charging-subsidy.xlsx; filename*=UTF-8''{quote(filename)}",
            "Cache-Control": "no-store",
        },
    )
    db.add(
        Audit(
            user_id=user.id,
            action="statistics.charging_subsidy.export",
            detail={
                "period": report.period,
                "start_date": str(report.start_date),
                "dimension": report.dimension,
                "rows": report.total,
            },
        )
    )
    db.commit()
    return result
