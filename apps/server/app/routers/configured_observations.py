"""Scoped durable configured observations; explicit demo write capability."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import configured_alerts
from app.authorization import authorize_business_write
from app.configured_alert_contracts import (
    ObservationEquipmentPage,
    ObservationEvidence,
    ObservationInput,
    ObservationPage,
    ObservationResult,
    ObservationRow,
    ObservationStationPage,
)
from app.dependencies import DB, Actor, SnapshotActor
from app.models import Audit, ConfiguredAlertObservation, Equipment, Operator, Station
from app.reporting import SnapshotDB
from app.scopes import station_scope

router = APIRouter(
    prefix="/api/v1/alerts/configured-observations", tags=["configured alert observations"]
)


@router.post("", response_model=ObservationResult, status_code=201)
def observe(body: ObservationInput, request: Request, user: Actor, db: DB, response: Response):
    authorize_business_write(db, user, request, "alerts.samples")
    try:
        result = configured_alerts.observe(db, user, body)
        # Receipt replay has no extra event or audit side effects.
        record = db.get(ConfiguredAlertObservation, result.observation_id)
        if not db.scalar(
            select(Audit.id).where(
                Audit.action == "alerts.observation.accept", Audit.object_id == record.id
            )
        ):
            db.add(
                Audit(
                    user_id=user.id,
                    action="alerts.observation.accept",
                    object_id=record.id,
                    detail={
                        "rule_id": body.rule_id,
                        "station_id": body.station_id,
                        "outcome": result.outcome,
                        "event_id": result.event_id,
                    },
                )
            )
        db.flush()
        validated = ObservationResult.model_validate(result.model_dump())
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            409, "观测编号或事件状态已变化，整次观测未重复提交；请核对原请求回执"
        ) from error
    response.headers["Cache-Control"] = "no-store"
    return validated


@router.get("", response_model=ObservationPage)
def listing(
    user: SnapshotActor,
    db: SnapshotDB,
    response: Response,
    station_id: int | None = Query(None, ge=1, le=2147483647),
    rule_id: int | None = Query(None, ge=1, le=2147483647),
    event_id: int | None = Query(None, ge=1, le=2147483647),
    request_key: UUID | None = None,
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "无权查看告警观测")
    o = ConfiguredAlertObservation
    stmt = station_scope(
        select(o, Station.name, Operator.name)
        .join(Station, Station.id == o.station_id)
        .join(Operator, Operator.id == o.operator_id),
        user,
    )
    if user.role == "operator":
        stmt = stmt.where(o.operator_id == user.operator_id)
    for value, column in [(station_id, o.station_id), (rule_id, o.rule_id), (event_id, o.event_id)]:
        if value is not None:
            stmt = stmt.where(column == value)
    if request_key is not None:
        stmt = stmt.where(o.request_key == str(request_key))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.order_by(o.id.desc()).offset((page - 1) * page_size).limit(page_size))
    result = ObservationPage(
        items=[
            ObservationRow(
                id=row.id,
                request_key=row.request_key,
                station_id=row.station_id,
                station_name=station,
                operator_id=row.operator_id,
                operator_name=operator,
                created_by=row.created_by,
                created_at=row.created_at.isoformat(),
                evidence=ObservationEvidence.model_validate(row.evidence),
                result=ObservationResult.model_validate(row.result),
            )
            for row, station, operator in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/stations", response_model=ObservationStationPage)
def station_options(
    user: SnapshotActor,
    db: SnapshotDB,
    response: Response,
    q: str = Query("", max_length=150),
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "无权查看观测场站")
    stmt = station_scope(select(Station), user)
    if q:
        stmt = stmt.where(Station.name.contains(q, autoescape=True))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Station.id).offset((page - 1) * page_size).limit(page_size))
    response.headers["Cache-Control"] = "no-store"
    return {
        "items": [{"id": s.id, "name": s.name} for s in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/equipment", response_model=ObservationEquipmentPage)
def equipment_options(
    user: SnapshotActor,
    db: SnapshotDB,
    response: Response,
    station_id: int = Query(..., ge=1, le=2147483647),
    q: str = Query("", max_length=150),
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "无权查看观测设备")
    if not db.scalar(station_scope(select(Station.id), user).where(Station.id == station_id)):
        raise HTTPException(404, "场站不存在或不可访问")
    stmt = select(Equipment).where(Equipment.station_id == station_id)
    if q:
        stmt = stmt.where(
            Equipment.name.contains(q, autoescape=True)
            | Equipment.external_id.contains(q, autoescape=True)
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Equipment.id).offset((page - 1) * page_size).limit(page_size))
    response.headers["Cache-Control"] = "no-store"
    return {
        "items": [
            {"id": s.id, "name": s.name, "external_id": s.external_id, "power_kw": str(s.power_kw)}
            for s in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
