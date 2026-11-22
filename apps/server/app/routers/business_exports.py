"""Scoped full-query CSV reports; historical values never come from today's record."""

import csv
import io
from types import SimpleNamespace
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import load_only

from app import rating_results
from app.authorization import require
from app.business_dates import Dates, submission_time
from app.dependencies import SnapshotActor
from app.models import (
    Audit,
    Operator,
    RatingAction,
    RatingApplication,
    Station,
    SubsidyAction,
    SubsidyClaim,
    SubsidyDeviceLine,
    SubsidyPolicy,
    User,
)
from app.reporting import SnapshotDB
from app.routers.rating import applications_statement
from app.routers.statistics import csv_cell
from app.routers.subsidy import DeviceLineFilters, claims_statement, device_lines_statement
from app.subsidy_execution_contracts import DeviceLineResponse

router = APIRouter(tags=["business reports"])
CSV = {200: {"content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}}}}
LIMIT = 10000


def filters(
    dates: Dates,
    q: str = Query("", max_length=150),
    status: str = Query("", max_length=30),
    record_id: int | None = Query(None, ge=1, le=9223372036854775807),
    kind: str = Query("", max_length=30),
):
    return {"q": q, "status": status, "record_id": record_id, "kind": kind, "dates": dates}


Filters = Annotated[dict, Depends(filters)]


def subsidy_filters(
    params: Filters,
    station_id: int | None = Query(None, ge=1, le=2147483647),
    application_year: int | None = Query(None, ge=2000, le=9998),
):
    return {**params, "station_id": station_id, "application_year": application_year}


SubsidyFilters = Annotated[dict, Depends(subsidy_filters)]


def statement(user, params, module):
    if module == "subsidy":
        if params["kind"]:
            raise HTTPException(422, "补贴导出不支持评星类型条件")
        return claims_statement(
            user,
            params["q"],
            params["status"],
            params["record_id"],
            params["dates"],
            station_id=params["station_id"],
            application_year=params["application_year"],
        )
    return applications_statement(
        user, params["q"], params["status"], params["record_id"], params["kind"], params["dates"]
    )


def response(db, user, module, report, headers, rows):
    if len(rows) > LIMIT:
        raise HTTPException(422, "单次最多导出10000条，请缩小查询范围；未截断导出")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows([csv_cell(value) for value in row] for row in rows)
    db.add(Audit(user_id=user.id, action=f"{module}.{report}.export", detail={"rows": len(rows)}))
    db.commit()
    return Response(
        "\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="ronghub-{module}-{report}.csv"',
            "Cache-Control": "no-store",
        },
    )


def guard(db, user, module):
    require(db, user, module + ".read")
    require(db, user, module + ".export")


@router.get("/api/v1/subsidy/claims/export", response_class=Response, responses=CSV)
def subsidy_list(user: SnapshotActor, db: SnapshotDB, params: SubsidyFilters):
    guard(db, user, "subsidy")
    c = SubsidyClaim
    stmt = (
        statement(user, params, "subsidy")
        .with_only_columns(
            c.id,
            c.station_id,
            Station.name,
            Operator.name,
            Station.district,
            c.start_date,
            c.end_date,
            SubsidyPolicy.code,
            SubsidyPolicy.version,
            c.total_kwh,
            c.base_amount,
            c.adjusted_amount,
            c.status,
            c.created_at,
            submission_time(SubsidyClaim).label("last_submitted_at"),
            c.version,
            c.is_demo,
            c.snapshot["application_year"].as_integer(),
        )
        .join(SubsidyPolicy, SubsidyPolicy.id == c.policy_id)
        .order_by(c.id.desc())
        .limit(LIMIT + 1)
    )
    return response(
        db,
        user,
        "subsidy",
        "claims",
        [
            "申请编号",
            "场站编号",
            "当前场站名称",
            "当前运营商名称",
            "当前区域",
            "申请开始日期",
            "申请结束日期",
            "政策代码",
            "政策版本",
            "有效电量(kWh)",
            "核算金额(元)",
            "调整后金额(元，非到账证明)",
            "当前办理状态",
            "创建时间(带时区)",
            "最近提交时间(带时区，缺失不以创建时间代替)",
            "记录版本",
            "演示标识",
            "申请年度",
        ],
        list(db.execute(stmt)),
    )


@router.get("/api/v1/ratings/applications/export", response_class=Response, responses=CSV)
def rating_list(user: SnapshotActor, db: SnapshotDB, params: Filters):
    guard(db, user, "ratings")
    a = RatingApplication
    stmt = (
        statement(user, params, "ratings")
        .with_only_columns(
            a.id,
            a.kind,
            a.parent_id,
            a.station_id,
            Station.name,
            Operator.name,
            Station.district,
            a.standard_id,
            a.calculation["total"].as_string(),
            a.calculation["stars"].as_integer().label("review_stars"),
            a.status,
            a.published_at,
            a.valid_until,
            a.created_at,
            submission_time(RatingApplication).label("last_submitted_at"),
            a.version,
            a.is_demo,
            Station.status.label("current_station_status"),
            Station.external_id.label("station_code"),
        )
        .order_by(a.id.desc())
        .limit(LIMIT + 1)
    )
    day = rating_results.local_day()
    records = db.execute(stmt).all()
    if len(records) > LIMIT:
        raise HTTPException(422, "单次最多导出10000条，请缩小查询范围；未截断导出")
    staff = user.role in {"regulator", "association"}
    current_results = (
        rating_results.latest_by_station(db, {row.station_id for row in records}) if staff else {}
    )
    rows = []
    for row in records:
        effective = rating_results.effective(
            SimpleNamespace(
                status=row.status,
                valid_until=row.valid_until,
                calculation={"stars": row.review_stars},
            ),
            today=day,
            station_status=row.current_station_status,
        )
        values = [*tuple(row)[:-2], effective["result_state"], effective["effective_stars"]]
        if staff:
            current = (
                rating_results.station_result(
                    current_results.get(row.station_id), row.current_station_status
                )
                or {}
            )
            values.extend(
                [
                    row.station_code,
                    current.get("result_state"),
                    current.get("effective_stars"),
                    current.get("valid_until"),
                ]
            )
        rows.append(values)
    return response(
        db,
        user,
        "ratings",
        "applications",
        [
            "申报编号",
            "申报类型",
            "关联申请编号",
            "场站编号",
            "当前场站名称",
            "当前运营商名称",
            "当前区域",
            "标准版本记录编号",
            "评审分数",
            "评审星级(非当前有效星级)",
            "当前办理状态",
            "公示时间(带时区)",
            "星级有效期至",
            "创建时间(带时区)",
            "最近提交时间(带时区，缺失不以创建时间代替)",
            "记录版本",
            "演示标识",
            "当前结果状态",
            "当前有效星级",
            *(
                ["来源场站编号", "场站当前结果状态", "场站当前有效星级", "场站当前结果有效期"]
                if staff
                else []
            ),
        ],
        rows,
    )


def process(user, db, params, module):
    guard(db, user, module)
    model, action, fk = (
        (SubsidyClaim, SubsidyAction, SubsidyAction.claim_id)
        if module == "subsidy"
        else (RatingApplication, RatingAction, RatingAction.application_id)
    )
    scoped_ids = statement(user, params, module).with_only_columns(model.id)
    stmt = (
        select(action, model.station_id, Station.name, Operator.name, User.username)
        .options(
            load_only(
                action.id,
                fk,
                action.actor_id,
                action.action,
                action.comment,
                action.created_at,
                action.transition_snapshot,
                *([SubsidyAction.delta] if module == "subsidy" else []),
            )
        )
        .join(
            model,
            model.id == fk,
        )
        .join(Station, Station.id == model.station_id)
        .join(Operator, Operator.id == model.operator_id)
        .join(
            User,
            User.id == action.actor_id,
        )
        .where(model.id.in_(scoped_ids))
        .order_by(model.id.desc(), action.id)
        .limit(LIMIT + 1)
    )
    records = list(db.execute(stmt))
    if len(records) > LIMIT:
        raise HTTPException(422, "单次最多导出10000条过程记录，请缩小查询范围；未截断导出")
    headers = [
        "申请编号",
        "动作编号",
        "场站编号",
        "当前场站名称",
        "当前运营商名称",
        "办理账号",
        "办理时间(带时区)",
        "动作",
        "办理意见",
        "历史快照状态",
    ]
    fields = (
        ["status", "version", "base_amount", "adjusted_amount"]
        if module == "subsidy"
        else ["status", "version", "total_score", "stars", "valid_until"]
    )
    labels = (
        ["办理状态", "版本", "核算金额(元)", "调整后金额(元)"]
        if module == "subsidy"
        else ["办理状态", "版本", "评审分数", "评审星级", "有效期至"]
    )
    headers += [prefix + label for prefix in ["变更前", "变更后"] for label in labels]
    if module == "subsidy":
        headers += ["本次调整额(元)"]
    rows = []
    for record, sid, station, operator, actor in records:
        frozen = record.transition_snapshot
        row = [
            getattr(record, "claim_id" if module == "subsidy" else "application_id"),
            record.id,
            sid,
            station,
            operator,
            actor,
            record.created_at,
            record.action,
            record.comment,
            "已留存" if frozen is not None else "旧记录未留存，未使用当前值补写",
        ]
        for side in ["before", "after"]:
            values = (frozen or {}).get(side) or {}
            row.extend(values.get(key) for key in fields)
        if module == "subsidy":
            row.append(record.delta)
        rows.append(row)
    return response(db, user, module, "process", headers, rows)


@router.get("/api/v1/subsidy/claims/process/export", response_class=Response, responses=CSV)
def subsidy_process(user: SnapshotActor, db: SnapshotDB, params: SubsidyFilters):
    return process(user, db, params, "subsidy")


@router.get("/api/v1/ratings/applications/process/export", response_class=Response, responses=CSV)
def rating_process(user: SnapshotActor, db: SnapshotDB, params: Filters):
    return process(user, db, params, "ratings")


@router.get(
    "/api/v1/subsidy/claims/{claim_id}/device-lines/export", response_class=Response, responses=CSV
)
def subsidy_device_lines_export(
    claim_id: int, user: SnapshotActor, db: SnapshotDB, filters: DeviceLineFilters
):
    guard(db, user, "subsidy")
    claim, statement = device_lines_statement(db, user, claim_id, filters)
    if claim.status not in {"approved", "rejected", "disbursed"}:
        raise HTTPException(409, "审核完成后可导出设备核算结果")
    rows = []
    for record in db.scalars(statement.order_by(SubsidyDeviceLine.fact_id).limit(LIMIT + 1)):
        line = DeviceLineResponse(
            claim_id=claim_id, fact_id=record.fact_id, snapshot=record.snapshot
        )
        source = line.snapshot.source
        rows.append(
            [
                claim_id,
                claim.status,
                source.day.isoformat(),
                source.equipment_name,
                source.equipment_external_id,
                source.equipment_type,
                source.power_kw,
                source.production_date.isoformat() if source.production_date else None,
                source.energy_kwh,
                line.fault_pct,
                line.offline_pct,
                line.snapshot.amount,
                "达标" if line.snapshot.eligible else "未达标",
            ]
        )
    return response(
        db,
        user,
        "subsidy",
        "device-review",
        [
            "申请编号",
            "申请审核状态",
            "观测日期",
            "设备名称",
            "设备编码",
            "设备类型",
            "功率(kW)",
            "生产日期",
            "冻结日计量(kWh)",
            "故障率(%)",
            "离线率(%)",
            "设备日汇算金额(元，系数前)",
            "设备日核算结论",
        ],
        rows,
    )
