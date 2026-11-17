"""Authorized subsidy aggregates and bounded all-or-nothing CSV exports."""

import csv
import io
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, model_validator
from sqlalchemy import select

from app import subsidy_statistics as statistics
from app.authorization import require
from app.contracts import OperatorList
from app.dependencies import SnapshotActor
from app.models import Audit, Operator, Station
from app.reporting import SnapshotDB
from app.routers.statistics import csv_cell
from app.scopes import station_scope

router = APIRouter(prefix="/api/v1/subsidy/statistics", tags=["subsidy statistics"])


class Filters(BaseModel):
    start_date: date
    end_date: date
    dimension: statistics.Dimension = "operator"
    status: statistics.Status = "accepted"
    operator_id: Annotated[int | None, Query(ge=1, le=2147483647)] = None
    station_id: Annotated[int | None, Query(ge=1, le=2147483647)] = None

    @model_validator(mode="after")
    def valid_dates(self):
        if self.start_date > self.end_date or (self.end_date - self.start_date).days > 365:
            raise HTTPException(422, "统计范围最多366天且结束不早于开始")
        return self


@router.get("", response_model=statistics.StatisticPage)
def listing(
    user: SnapshotActor,
    db: SnapshotDB,
    response: Response,
    filters: Annotated[Filters, Depends()],
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    rows, totals = statistics.collect(db, user, **filters.model_dump())
    response.headers["Cache-Control"] = "no-store"
    return statistics.StatisticPage(
        items=rows[(page - 1) * page_size : page * page_size],
        total=len(rows),
        page=page,
        page_size=page_size,
        totals=totals,
        notice=statistics.NOTICE,
        **filters.model_dump(include={"dimension", "status"}),
        start_date=filters.start_date.isoformat(),
        end_date=filters.end_date.isoformat(),
    )


@router.get(
    "/export",
    response_class=Response,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}}}},
)
def export(user: SnapshotActor, db: SnapshotDB, filters: Annotated[Filters, Depends()]):
    require(db, user, "subsidy.read")
    rows, totals = statistics.collect(db, user, **filters.model_dump())
    if len(rows) > 10000:
        raise HTTPException(422, "统计分组超过10000条，请缩小范围；未导出部分结果")
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    fields = list(statistics.StatisticRow.model_fields)
    writer.writerow(["维度", "申请状态范围", "完整周期开始", "完整周期结束", *fields])
    for row in rows:
        data = row.model_dump()
        writer.writerow(
            [
                filters.dimension,
                filters.status,
                filters.start_date.isoformat(),
                filters.end_date.isoformat(),
                *[csv_cell(data[key]) for key in fields],
            ]
        )
    writer.writerow(["口径", statistics.NOTICE])
    writer.writerow(["全量申请汇总（设备维度不分摊系数及差额）", totals.model_dump_json()])
    payload = ("\ufeff" + stream.getvalue()).encode("utf-8")
    db.add(
        Audit(
            user_id=user.id,
            action="subsidy.statistics.export",
            object_id=None,
            detail={"filters": filters.model_dump(mode="json"), "rows": len(rows)},
        )
    )
    db.commit()
    return Response(
        payload,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="subsidy-statistics.csv"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/operators", response_model=OperatorList)
def operators(user: SnapshotActor, db: SnapshotDB, response: Response):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "无权访问补贴统计运营商")
    rows = list(
        db.scalars(
            select(Operator)
            .where(Operator.id.in_(station_scope(select(Station.operator_id), user)))
            .order_by(Operator.id)
            .limit(10001)
        )
    )
    if len(rows) > 10000:
        raise HTTPException(422, "授权运营商超过10000，请先收窄账号范围")
    response.headers["Cache-Control"] = "no-store"
    return {"items": [{"id": row.id, "name": row.name} for row in rows]}
