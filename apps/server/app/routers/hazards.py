"""Independent hazard supervision with scoped evidence and separate export authority."""

import csv
import io
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from app import hazards
from app.authorization import authorize_business_write, require
from app.config import settings
from app.contracts import OkResponse
from app.dependencies import DB, Actor, SnapshotActor
from app.hourly_energy import BEIJING
from app.material_contracts import InspectionMaterialItem, binary_responses
from app.models import Audit, HazardAttachment, HazardCase, Operator, Station, now
from app.reporting import SnapshotDB
from app.routers.attachments import metadata
from app.routers.statistics import csv_cell
from app.scopes import station_scope
from app.storage import store_upload

router = APIRouter(prefix="/api/v1/hazards", tags=["hazards"])
EXPORT_LIMIT = 10000


class Filters(BaseModel):
    q: str
    status: str
    progress: str
    severity: str
    overdue: bool | None
    created_from: date | None
    created_to: date | None
    record_id: int | None


def filters(
    q: str = Query("", max_length=150),
    status: Literal["", "tracking", "closed"] = "",
    progress: Literal["", "not_started", "in_progress", "completed"] = "",
    severity: Literal["", "normal", "major"] = "",
    overdue: bool | None = None,
    created_from: date | None = None,
    created_to: date | None = None,
    record_id: int | None = Query(None, ge=1),
):
    if created_to == date.max or (
        created_from is not None and created_to is not None and created_from > created_to
    ):
        raise HTTPException(422, "创建日期范围不正确")
    return Filters(
        q=q,
        status=status,
        progress=progress,
        severity=severity,
        overdue=overdue,
        created_from=created_from,
        created_to=created_to,
        record_id=record_id,
    )


Selected = Annotated[Filters, Depends(filters)]


def statement(user, selected, at):
    stmt = hazards.scoped(user)
    if selected.q.strip():
        stmt = stmt.where(
            or_(
                *(
                    field.icontains(selected.q.strip(), autoescape=True)
                    for field in [
                        HazardCase.title,
                        HazardCase.station_name,
                        HazardCase.operator_name,
                    ]
                )
            )
        )
    for key in ["status", "progress", "severity", "record_id"]:
        value = getattr(selected, key)
        if value:
            stmt = stmt.where(getattr(HazardCase, "id" if key == "record_id" else key) == value)
    overdue = (
        (HazardCase.status == "tracking")
        & (HazardCase.progress != "completed")
        & (HazardCase.due_at < at)
    )
    if selected.overdue is not None:
        stmt = stmt.where(overdue if selected.overdue else ~overdue)
    if selected.created_from is not None:
        stmt = stmt.where(
            HazardCase.created_at >= datetime.combine(selected.created_from, time.min, BEIJING)
        )
    if selected.created_to is not None:
        stmt = stmt.where(
            HazardCase.created_at
            < datetime.combine(selected.created_to + timedelta(days=1), time.min, BEIJING)
        )
    return stmt


@router.get("/stations", response_model=hazards.HazardCandidates)
def candidates(
    user: SnapshotActor,
    db: SnapshotDB,
    response: Response,
    q: str = Query("", max_length=150),
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    require(db, user, "hazards.read")
    require(db, user, "hazards.create")
    if user.role != "regulator":
        raise HTTPException(403, "仅监管可选取督办目标")
    response.headers["Cache-Control"] = "no-store"
    stmt = station_scope(
        select(Station, Operator.name)
        .join(Operator, Operator.id == Station.operator_id)
        .where(Operator.enabled.is_(True)),
        user,
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
                "id": row.id,
                "name": row.name,
                "operator_id": row.operator_id,
                "operator_name": company,
                "district": row.district,
            }
            for row, company in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("", response_model=hazards.HazardPage)
def listing(
    user: SnapshotActor,
    db: SnapshotDB,
    response: Response,
    selected: Selected,
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    response.headers["Cache-Control"] = "no-store"
    at = now()
    stmt = statement(user, selected, at)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(
        stmt.order_by(HazardCase.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return {
        "items": [hazards.summary(row, at) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "query_at": at,
    }


@router.get("/export", response_class=Response, responses=binary_responses("text/csv"))
def export(user: SnapshotActor, db: SnapshotDB, selected: Selected):
    require(db, user, "hazards.read")
    at = now()
    rows = list(
        db.scalars(statement(user, selected, at).order_by(HazardCase.id).limit(EXPORT_LIMIT + 1))
    )
    if len(rows) > EXPORT_LIMIT:
        raise HTTPException(422, "导出超过10000条，请缩小查询范围")
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(
        [
            "隐患编号",
            "隐患名称",
            "登记时场站",
            "目标运营商",
            "隐患等级",
            "整改进度",
            "跟踪状态",
            "超期未完成",
            "创建时间（北京时间）",
            "整改期限（北京时间）",
            "关闭时间（北京时间）",
            "隐患说明",
            "演示数据",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                csv_cell(value)
                for value in [
                    row.id,
                    row.title,
                    row.station_name,
                    row.operator_name,
                    "重大（挂牌督办）" if row.severity == "major" else "一般",
                    {
                        "not_started": "未开始",
                        "in_progress": "整改中",
                        "completed": "企业已填报完成",
                    }[row.progress],
                    "跟踪中" if row.status == "tracking" else "已关闭",
                    "是" if hazards.summary(row, at)["overdue"] else "否",
                    row.created_at.astimezone(BEIJING).isoformat(),
                    row.due_at.astimezone(BEIJING).isoformat(),
                    row.closed_at.astimezone(BEIJING).isoformat() if row.closed_at else "",
                    row.description,
                    row.is_demo,
                ]
            ]
        )
    result = Response(
        stream.getvalue().encode("utf-8-sig"),
        media_type="text/csv",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="ronghub-hazards.csv"',
        },
    )
    db.add(
        Audit(
            user_id=user.id,
            action="hazards.export",
            detail={"object_type": "hazard_case", "rows": len(rows)},
        )
    )
    db.commit()
    return result


@router.post("", status_code=201, response_model=hazards.HazardDetail)
def create(body: hazards.HazardCreateInput, request: Request, user: Actor, db: DB):
    authorize_business_write(db, user, request, "hazards.create")
    row = hazards.create(db, user, body)
    result = hazards.output(db, row)
    db.commit()
    return result


@router.get("/{case_id}", response_model=hazards.HazardDetail)
def detail(case_id: int, user: SnapshotActor, db: SnapshotDB):
    return hazards.output(db, hazards.access(db, user, case_id))


@router.post("/{case_id}/actions", response_model=hazards.HazardDetail)
def action(case_id: int, body: hazards.HazardActionInput, request: Request, user: Actor, db: DB):
    authorize_business_write(
        db, user, request, "hazards.close" if body.action == "close" else "hazards.reply"
    )
    result = hazards.output(db, hazards.act(db, user, case_id, body))
    db.commit()
    return result


def writable(db, user, case_id):
    hazards.demo_only()
    row = hazards.access(db, user, case_id, lock=True)
    if user.role != "operator" or row.status != "tracking":
        raise HTTPException(409, "仅目标企业可在跟踪中维护未提交材料")
    return row


@router.post("/{case_id}/attachments", status_code=201, response_model=InspectionMaterialItem)
def upload(case_id: int, file: UploadFile, request: Request, user: Actor, db: DB):
    authorize_business_write(db, user, request, "hazards.materials")
    writable(db, user, case_id)
    if (
        db.scalar(
            select(func.count())
            .select_from(HazardAttachment)
            .where(HazardAttachment.case_id == case_id)
        )
        >= 30
    ):
        raise HTTPException(422, "每项隐患最多30份证明材料")
    values, path = store_upload(file)
    try:
        item = HazardAttachment(case_id=case_id, uploaded_by=user.id, **values)
        db.add(item)
        db.flush()
        result = InspectionMaterialItem.model_validate(
            {**metadata(item), "action_id": item.action_id}
        )
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    return result


@router.get(
    "/{case_id}/attachments/{attachment_id}/download",
    response_class=FileResponse,
    responses=binary_responses("application/pdf", "image/png", "image/jpeg"),
)
def download(case_id: int, attachment_id: int, user: SnapshotActor, db: SnapshotDB):
    hazards.access(db, user, case_id)
    item = db.get(HazardAttachment, attachment_id)
    if item is None or item.case_id != case_id:
        raise HTTPException(404, "证明材料不存在")
    path = Path(settings.storage_path).resolve() / item.storage_key
    if not path.is_file():
        raise HTTPException(404, "证明材料文件不可用")
    return FileResponse(
        path,
        filename=item.filename,
        media_type=item.content_type,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/{case_id}/attachments/{attachment_id}", response_model=OkResponse)
def remove(case_id: int, attachment_id: int, request: Request, user: Actor, db: DB):
    authorize_business_write(db, user, request, "hazards.materials")
    writable(db, user, case_id)
    item = db.get(HazardAttachment, attachment_id)
    if item is None or item.case_id != case_id:
        raise HTTPException(404, "证明材料不存在")
    if item.action_id is not None:
        raise HTTPException(409, "已提交证明材料不可删除")
    path = Path(settings.storage_path).resolve() / item.storage_key
    db.delete(item)
    db.commit()
    path.unlink(missing_ok=True)
    return {"ok": True}
