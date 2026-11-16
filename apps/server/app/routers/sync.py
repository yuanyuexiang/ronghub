from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import settings
from app.dependencies import DB, Actor

router = APIRouter()


class SyncInput(BaseModel):
    operator_id: int
    request_key: str = Field(min_length=8, max_length=100)


@router.post("/api/v1/sync/tasks", status_code=202)
def create_sync(body: SyncInput, user: Actor, db: DB):
    from app.models import Audit, Operator
    from app.sync import enqueue

    if user.role != "regulator":
        raise HTTPException(403, "仅监管人员可管理同步")
    if not settings.demo_mode:
        raise HTTPException(409, "客户适配器尚未配置，模拟接口仅限演示环境")
    if not db.get(Operator, body.operator_id):
        raise HTTPException(404, "运营商不存在")
    try:
        task = enqueue(db, body.operator_id, body.request_key)
    except ValueError as exc:
        raise HTTPException(409, "请求标识冲突") from exc
    db.add(
        Audit(user_id=user.id, action="sync.enqueue", object_id=task.id, detail={"source": "demo"})
    )
    db.commit()
    return {"id": task.id, "status": task.status}


@router.get("/api/v1/sync/tasks")
def sync_tasks(user: Actor, db: DB):
    from app.models import SyncTask

    if user.role != "regulator":
        raise HTTPException(403, "仅监管人员可管理同步")
    rows = db.scalars(select(SyncTask).order_by(SyncTask.id.desc()).limit(50))
    return {
        "items": [
            {
                "id": t.id,
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
