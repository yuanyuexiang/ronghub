from datetime import date, datetime

from fastapi import APIRouter

from app import dashboard_operations
from app.charging_report import BEIJING, Period
from app.dependencies import SnapshotActor
from app.reporting import SnapshotDB

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


@router.get("/operations", response_model=dashboard_operations.OperationsDashboard)
def report(user: SnapshotActor, db: SnapshotDB, period: Period = "day", period_date: date | None = None):
    point = period_date or datetime.now(BEIJING).date()
    return dashboard_operations.report(db, user, period, point)
