"""Scoped independent coverage facts; adapter writes have no public HTTP bypass."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import func, select

from app.alert_period_contracts import (
    Kind,
    Period,
    PeriodFactPage,
    PeriodFactResponse,
    SourcePeriod,
    aggregate,
)
from app.dependencies import SnapshotActor
from app.models import AlertPeriodFact, Operator, Station
from app.reporting import SnapshotDB
from app.scopes import station_scope

router = APIRouter(prefix="/api/v1/alerts/period-facts", tags=["alert period sources"])


@router.get("", response_model=PeriodFactPage)
def listing(
    user: SnapshotActor,
    db: SnapshotDB,
    response: Response,
    station_id: int | None = Query(None, ge=1, le=2147483647),
    metric: Kind | None = None,
    period: Period | None = None,
    start: date | None = None,
    end: date | None = None,
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "无权查看告警周期来源")
    if start and end and start > end:
        raise HTTPException(422, "开始日期不得晚于结束日期")
    f = AlertPeriodFact
    stmt = station_scope(
        select(f, Station.name, Operator.name)
        .join(Station, Station.id == f.station_id)
        .join(Operator, Operator.id == f.operator_id),
        user,
    )
    if user.role == "operator":
        stmt = stmt.where(f.operator_id == user.operator_id)
    for value, column in [(station_id, f.station_id), (metric, f.metric), (period, f.period)]:
        if value is not None:
            stmt = stmt.where(column == value)
    if start:
        stmt = stmt.where(f.period_start >= start)
    if end:
        stmt = stmt.where(f.period_start <= end)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(
        stmt.order_by(f.period_closed_at.desc(), f.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = []
    for fact, station_name, operator_name in rows:
        record = SourcePeriod.model_validate(fact.payload)
        numerator, denominator, reason = aggregate(record)
        with localcontext() as context:
            context.prec = 50
            percent = (
                (numerator * 100 / denominator).quantize(Decimal(".000001"), rounding=ROUND_HALF_UP)
                if reason is None
                else None
            )
        items.append(
            PeriodFactResponse(
                id=fact.id,
                station_id=fact.station_id,
                station_name=station_name,
                operator_id=fact.operator_id,
                operator_name=operator_name,
                source=fact.source,
                version=fact.version,
                source_record=record,
                percent=str(percent) if percent is not None else None,
                numerator_seconds=str(numerator) if reason is None else None,
                denominator_seconds=str(denominator) if reason is None else None,
                quality_reason=reason,
                is_demo=fact.is_demo,
                verified=False,
                synced_at=fact.synced_at.isoformat(),
            )
        )
    response.headers["Cache-Control"] = "no-store"
    return PeriodFactPage(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        notice="独立演示来源，设备集合及服务秒数由来源显式声明；未知不按零或正常处理，非正式核定指标。日期按周期开始日筛选。",
    )
