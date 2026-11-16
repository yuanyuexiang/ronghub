import csv
import io
from datetime import date

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app import statistics
from app.dependencies import DB, Actor
from app.models import Audit

router = APIRouter(prefix="/api/v1/statistics", tags=["statistics"])


@router.get("")
def report(
    start_date: date,
    end_date: date,
    user: Actor,
    db: DB,
    district: str = "",
    operator_id: int | None = None,
    granularity: str = "day",
    dimension: str = "station",
):
    return statistics.report(
        db, user, start_date, end_date, district, operator_id, granularity, dimension
    )


def csv_cell(value):
    text = str(value) if value is not None else "暂无数据"
    # CSV quoted fields alone do not prevent spreadsheet formula execution.
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else text


@router.get("/export")
def export(
    start_date: date,
    end_date: date,
    user: Actor,
    db: DB,
    district: str = "",
    operator_id: int | None = None,
):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "当前角色无权导出电量明细")
    data = statistics.report(db, user, start_date, end_date, district, operator_id)
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
    return Response(
        "\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="ronghub-energy.csv"'},
    )


@router.get("/analysis/export")
def export_analysis(
    start_date: date,
    end_date: date,
    user: Actor,
    db: DB,
    district: str = "",
    operator_id: int | None = None,
    granularity: str = "month",
    dimension: str = "station",
):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "当前角色无权导出电量分析")
    data = statistics.report(
        db, user, start_date, end_date, district, operator_id, granularity, dimension
    )
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
    return Response(
        "\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="ronghub-energy-analysis.csv"'},
    )
