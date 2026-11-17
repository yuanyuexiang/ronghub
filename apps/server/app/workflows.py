"""Demonstration workflow; customer state/materal rules must be approved before production."""

from fastapi import HTTPException
from sqlalchemy import select

from app.business_audit import state, transition
from app.config import settings
from app.models import ApprovalAction, Station, StationApplication, now
from app.scopes import records as scoped_records
from app.scopes import station_scope

KINDS = {"access", "acceptance", "commission", "decommission", "change"}
PRECONDITIONS = {
    "access": {"pending"},
    "acceptance": {"access_approved"},
    "commission": {"accepted"},
    "decommission": {"operating"},
    "change": {"access_approved", "accepted", "operating"},
}
TARGETS = {
    "access": "access_approved",
    "acceptance": "accepted",
    "commission": "operating",
    "decommission": "decommissioned",
}


def allowed_demo_rules():
    if not settings.demo_mode:
        raise HTTPException(409, "审批规则尚待客户确认；当前演示规则不能用于正式业务")


def create(db, user, station_id, kind, payload):
    allowed_demo_rules()
    if user.role != "operator" or user.operator_id is None:
        raise HTTPException(403, "仅运营商可发起场站申请")
    if kind not in KINDS:
        raise HTTPException(422, "不支持的申请类型")
    station = db.scalar(
        station_scope(select(Station), user)
        .where(Station.id == station_id, Station.operator_id == user.operator_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not station:
        raise HTTPException(404, "场站不存在或不可访问")
    if station.status not in PRECONDITIONS[kind]:
        raise HTTPException(409, "当前场站状态不允许此申请")
    if db.scalar(
        select(StationApplication.id).where(
            StationApplication.station_id == station_id,
            StationApplication.status.in_(["draft", "submitted", "rejected"]),
        )
    ):
        raise HTTPException(409, "该场站已有未结束申请，请先处理或撤回")
    from app.application_evidence import normalize_payload

    payload = normalize_payload(payload)
    application = StationApplication(
        station_id=station_id,
        operator_id=user.operator_id,
        applicant_id=user.id,
        kind=kind,
        payload=payload,
    )
    db.add(application)
    db.flush()
    db.add(ApprovalAction(application_id=application.id, actor_id=user.id, action="create"))
    transition(
        db,
        user,
        "applications.create",
        application.id,
        None,
        state(application),
        "station_application",
    )
    return application


def act(db, user, application_id, action, version, comment, review_token=None):
    allowed_demo_rules()
    a = db.scalar(
        scoped_records(select(StationApplication), StationApplication, user)
        .where(StationApplication.id == application_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not a or (user.role == "operator" and a.operator_id != user.operator_id):
        raise HTTPException(404, "申请不存在或不可访问")
    if user.role not in {"operator", "regulator"}:
        raise HTTPException(403, "无权处理申请")
    before = state(a)
    if a.version != version:
        raise HTTPException(409, "记录已更新，请刷新后重试")
    if action in {"submit", "withdraw"}:
        if user.role != "operator":
            raise HTTPException(403, "仅所属运营商可提交或撤回")
        if action == "submit" and a.status not in {"draft", "rejected"}:
            raise HTTPException(409, "当前状态不能提交")
        if action == "withdraw" and a.status not in {"draft", "submitted", "rejected"}:
            raise HTTPException(409, "当前状态不能撤回")
        if action == "submit":
            from app.application_evidence import capture

            submitted_at = capture(db, user, a, review_token)
        a.status = "submitted" if action == "submit" else "withdrawn"
    elif action in {"approve", "reject"}:
        if user.role != "regulator":
            raise HTTPException(403, "仅监管人员可审核")
        if a.status != "submitted":
            raise HTTPException(409, "仅待审核申请可处理")
        if not comment.strip():
            raise HTTPException(422, "请填写审核意见")
        station = db.scalar(
            station_scope(select(Station), user)
            .where(Station.id == a.station_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if not station:
            raise HTTPException(404, "场站不存在或不在授权区域")
        before.update(station_status=station.status, station_version=station.version)
        if action == "approve":
            if station.status not in PRECONDITIONS[a.kind]:
                raise HTTPException(409, "场站状态已变化，无法通过此申请")
            if a.kind == "change":
                for key in ["name", "address"]:
                    value = a.payload.get(key)
                    if value:
                        if not isinstance(value, str) or len(value) > (
                            150 if key == "name" else 255
                        ):
                            raise HTTPException(422, "变更字段无效")
                        setattr(station, key, value)
            else:
                station.status = TARGETS[a.kind]
            station.version += 1
        a.status = "approved" if action == "approve" else "rejected"
    else:
        raise HTTPException(422, "不支持的操作")
    a.version += 1
    a.updated_at = now()
    db.add(
        ApprovalAction(
            application_id=a.id,
            actor_id=user.id,
            action=action,
            comment=comment,
            **({"created_at": submitted_at} if action == "submit" else {}),
        )
    )
    after = state(a)
    if action in {"approve", "reject"}:
        after.update(station_status=station.status, station_version=station.version)
    transition(db, user, "applications." + action, a.id, before, after, "station_application")
    return a


def output(a):
    return {
        "id": a.id,
        "station_id": a.station_id,
        "operator_id": a.operator_id,
        "kind": a.kind,
        "status": a.status,
        "version": a.version,
        "payload": a.payload,
        "rule_version": a.rule_version,
        "created_at": a.created_at.isoformat(),
    }
