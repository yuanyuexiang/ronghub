"""Creator-private inspection preparation, scoped selection and explicit atomic publication."""

from typing import Literal

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import func, or_, select

from app import inspection_plans as plans
from app.authorization import authorize_business_write
from app.dependencies import DB, Actor, SnapshotActor
from app.models import InspectionPlan, Operator, Station
from app.reporting import SnapshotDB
from app.scopes import station_scope

router = APIRouter(prefix="/api/v1/inspection-plans", tags=["inspection-plans"])


def finish(db, user, row):
    result = plans.InspectionPlanDetail.model_validate(plans.output(db, user, row))
    db.commit()
    return result


@router.get("", response_model=plans.InspectionPlanPage)
def listing(
    user: SnapshotActor,
    db: SnapshotDB,
    response: Response,
    q: str = Query("", max_length=150),
    status: Literal["draft", "published", "deleted"] = "draft",
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(10, ge=1, le=100),
):
    plans.planner(db, user)
    response.headers["Cache-Control"] = "no-store"
    stmt = select(InspectionPlan).where(
        InspectionPlan.created_by == user.id, InspectionPlan.status == status
    )
    if q.strip():
        stmt = stmt.where(InspectionPlan.title.icontains(q.strip(), autoescape=True))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(
        stmt.order_by(InspectionPlan.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return {
        "items": [plans.summary(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/stations", response_model=plans.InspectionPlanStationPage)
def stations(
    user: SnapshotActor,
    db: SnapshotDB,
    response: Response,
    q: str = Query("", max_length=150),
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    plans.planner(db, user)
    response.headers["Cache-Control"] = "no-store"
    stmt = station_scope(
        select(Station, Operator.name).join(Operator, Operator.id == Station.operator_id), user
    )
    if q.strip():
        stmt = stmt.where(
            or_(
                Station.name.icontains(q.strip(), autoescape=True),
                Operator.name.icontains(q.strip(), autoescape=True),
            )
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.order_by(Station.id).offset((page - 1) * page_size).limit(page_size))
    return {
        "items": [
            {
                "id": station.id,
                "name": station.name,
                "operator_name": company,
                "district": station.district,
            }
            for station, company in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{plan_id}", response_model=plans.InspectionPlanDetail)
def detail(plan_id: int, user: SnapshotActor, db: SnapshotDB, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return plans.output(db, user, plans.access(db, user, plan_id))


@router.post("", response_model=plans.InspectionPlanDetail, status_code=201)
def create(body: plans.InspectionPlanCreate, request: Request, user: Actor, db: DB):
    authorize_business_write(db, user, request, "inspections.assign")
    return finish(db, user, plans.create(db, user, body.request_key))


@router.put("/{plan_id}", response_model=plans.InspectionPlanDetail)
def edit(plan_id: int, body: plans.InspectionPlanEdit, request: Request, user: Actor, db: DB):
    authorize_business_write(db, user, request, "inspections.assign")
    return finish(db, user, plans.edit(db, user, plan_id, body))


@router.post("/{plan_id}/publish", response_model=plans.InspectionPlanDetail)
def publish(plan_id: int, body: plans.InspectionPlanVersion, request: Request, user: Actor, db: DB):
    authorize_business_write(db, user, request, "inspections.assign")
    return finish(db, user, plans.publish(db, user, plan_id, body.version))


@router.delete("/{plan_id}", response_model=plans.InspectionPlanDetail)
def remove(plan_id: int, request: Request, user: Actor, db: DB, version: int = Query(ge=1)):
    authorize_business_write(db, user, request, "inspections.assign")
    return finish(db, user, plans.remove(db, user, plan_id, version))
