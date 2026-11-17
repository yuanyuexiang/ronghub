from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import settings
from app.contracts import OperatorList
from app.dependencies import DB, Actor, SnapshotActor
from app.operational_contracts import SyncAccepted, SyncTaskList
from app.reporting import SnapshotDB

router = APIRouter()


class SyncInput(BaseModel):
    operator_id: int
    request_key: str = Field(min_length=8, max_length=100)


@router.post("/api/v1/sync/tasks", status_code=202, response_model=SyncAccepted)
def create_sync(body: SyncInput, user: Actor, db: DB, response: Response):
    from app.models import Audit, Operator
    from app.sync import enqueue_once

    if user.role != "regulator":
        raise HTTPException(403, "仅监管人员可管理同步")
    if not settings.demo_mode:
        raise HTTPException(409, "客户适配器尚未配置，模拟接口仅限演示环境")
    if not db.get(Operator, body.operator_id):
        raise HTTPException(404, "运营商不存在")
    try:
        task, created = enqueue_once(db, body.operator_id, body.request_key)
    except ValueError as exc:
        raise HTTPException(409, "请求标识冲突") from exc
    if created:
        db.add(
            Audit(
                user_id=user.id, action="sync.enqueue", object_id=task.id, detail={"source": "demo"}
            )
        )
    result = SyncAccepted.model_validate({"id": task.id, "status": task.status})
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/api/v1/sync/tasks", response_model=SyncTaskList)
def sync_tasks(
    user: SnapshotActor,
    db: SnapshotDB,
    response: Response,
    request_key: str | None = Query(None, min_length=8, max_length=100),
):
    response.headers["Cache-Control"] = "no-store"
    from app.models import SyncTask

    if user.role != "regulator":
        raise HTTPException(403, "仅监管人员可管理同步")
    stmt = select(SyncTask)
    if request_key is not None:
        stmt = stmt.where(SyncTask.request_key == request_key)
    rows = db.scalars(stmt.order_by(SyncTask.id.desc()).limit(50))
    return {
        "items": [
            {
                "id": t.id,
                "request_key": t.request_key,
                "source": t.source,
                "status": t.status,
                "operator_id": t.operator_id,
                "attempts": t.attempts,
                "error": t.error,
                "result": t.result,
                "created_at": t.created_at.isoformat(),
            }
            for t in rows
        ]
    }


@router.get("/api/v1/sync/operators", response_model=OperatorList)
def operator_choices(user: SnapshotActor, db: SnapshotDB, response: Response):
    from app.models import Operator

    if user.role != "regulator":
        raise HTTPException(403, "仅监管人员可管理同步")
    response.headers["Cache-Control"] = "no-store"
    return {
        "items": [
            {"id": oid, "name": name}
            for oid, name in db.execute(select(Operator.id, Operator.name).order_by(Operator.id))
        ]
    }
