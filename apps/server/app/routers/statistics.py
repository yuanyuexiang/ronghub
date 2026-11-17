import csv
import io
from datetime import date

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app import statistics
from app.dependencies import SnapshotActor
from app.models import Audit
from app.query_contracts import EnergyStatistics
from app.reporting import SnapshotDB

router = APIRouter(prefix="/api/v1/statistics", tags=["statistics"])


@router.get("", response_model=EnergyStatistics)
def report(
    start_date: date,
    end_date: date,
    user: SnapshotActor,
    db: SnapshotDB,
    response: Response,
    district: str = "",
    operator_id: int | None = Query(None, ge=1, le=2147483647),
    granularity: str = "day",
    dimension: str = "station",
):
    response.headers["Cache-Control"] = "no-store"
    return statistics.report(
        db, user, start_date, end_date, district, operator_id, granularity, dimension
    )


def csv_cell(value):
    text = str(value) if value is not None else "暂无数据"
    # CSV quoted fields alone do not prevent spreadsheet formula execution.
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else text


def csv_response(body, filename):
    return Response(
        "\ufeff" + body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get(
    "/export",
    response_class=Response,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string"}}}}},
)
def export(
    start_date: date,
    end_date: date,
    user: SnapshotActor,
    db: SnapshotDB,
    district: str = "",
    operator_id: int | None = Query(None, ge=1, le=2147483647),
):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "当前角色无权导出电量明细")
    data = EnergyStatistics.model_validate(
        statistics.report(db, user, start_date, end_date, district, operator_id)
    ).model_dump()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["场站ID", "场站名称", "区域", "运营商", "开始日期", "结束日期", "观测电量kWh", "口径"]
    )
    for s in data["ranking"]:
        writer.writerow(
            [
                csv_cell(v)
                for v in [
                    s["id"],
                    s["name"],
                    s["district"],
                    s["operator"],
                    start_date,
                    end_date,
                    s["kwh"],
                    "演示观测记录，非结算数据",
                ]
            ]
        )
    result = csv_response(buffer.getvalue(), "ronghub-energy.csv")
    db.add(
        Audit(
            user_id=user.id,
            action="statistics.export",
            detail={
                "start_date": str(start_date),
                "end_date": str(end_date),
                "district": district,
                "operator_id": operator_id,
                "stations": len(data["ranking"]),
            },
        )
    )
    db.commit()
    return result


@router.get(
    "/analysis/export",
    response_class=Response,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string"}}}}},
)
def export_analysis(
    start_date: date,
    end_date: date,
    user: SnapshotActor,
    db: SnapshotDB,
    district: str = "",
    operator_id: int | None = Query(None, ge=1, le=2147483647),
    granularity: str = "month",
    dimension: str = "station",
):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "当前角色无权导出电量分析")
    data = EnergyStatistics.model_validate(
        statistics.report(
            db, user, start_date, end_date, district, operator_id, granularity, dimension
        )
    ).model_dump()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "周期",
            "开始日期",
            "结束日期",
            "观测电量kWh",
            "有记录场站日",
            "应有场站日",
            "观测同比%",
            "同比不可用原因",
            "观测环比%",
            "环比不可用原因",
            "口径",
        ]
    )
    for period in data["timeline"]:
        yoy = period["comparisons"]["year_over_year"]
        pop = period["comparisons"]["period_over_period"]
        writer.writerow(
            [
                csv_cell(v)
                for v in [
                    period["period"],
                    period["start_date"],
                    period["end_date"],
                    period["kwh"],
                    period["station_days"],
                    period["expected_station_days"],
                    yoy["observed_change_percent"],
                    yoy["unavailable_reason"] or "",
                    pop["observed_change_percent"],
                    pop["unavailable_reason"] or "",
                    data["basis"],
                ]
            ]
        )
    result = csv_response(buffer.getvalue(), "ronghub-energy-analysis.csv")
    db.add(
        Audit(
            user_id=user.id,
            action="statistics.export",
            detail={
                "kind": "period_analysis",
                "start_date": str(start_date),
                "end_date": str(end_date),
                "district": district,
                "operator_id": operator_id,
                "granularity": granularity,
                "dimension": dimension,
            },
        )
    )
    db.commit()
    return result
