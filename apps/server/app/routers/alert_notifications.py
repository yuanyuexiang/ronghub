"""Private, versioned alert routing preferences and recipients."""

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import alert_notifications as routing
from app.alert_notification_contracts import (
    CATEGORIES,
    Category,
    DeliveryPage,
    DeliveryResponse,
    PreferenceInput,
    PreferenceList,
    PreferenceResponse,
    RecipientInput,
    RecipientPage,
    RecipientResponse,
    RecipientUpdate,
    RoutingPage,
    RoutingResponse,
)
from app.alerts import demo_only
from app.authorization import authorize_business_write, require
from app.config import settings
from app.dependencies import DB, Actor, SnapshotActor
from app.models import (
    AlertNotificationPreference,
    AlertNotificationRouting,
    AlertRecipient,
    AlertSmsDelivery,
    Audit,
    now,
)
from app.reporting import SnapshotDB
from app.scopes import require_global
from app.sms import mask

router = APIRouter(prefix="/api/v1/alerts/notifications", tags=["private alert notifications"])


def manager(user, db):
    if user.role != "regulator":
        raise HTTPException(403, "仅监管人员可管理告警接收人及短信记录")
    require(db, user, "alerts.read")
    require(db, user, "alerts.notifications")
    require_global(user)


def authorize(request, user, db):
    authorize_business_write(db, user, request, "alerts.notifications")
    manager(user, db)
    demo_only()
    routing.lock(db, write=True)


def preference_output(row, category):
    return PreferenceResponse(
        category=category,
        label=CATEGORIES[category],
        version=row.version if row else 0,
        in_app_enabled=row.in_app_enabled if row else None,
        sms_enabled=row.sms_enabled if row else False,
        updated_by=row.updated_by if row else None,
        updated_at=row.updated_at.isoformat() if row else None,
        legacy_behavior=row is None,
    )


def recipient_output(row):
    return RecipientResponse(
        id=row.id,
        name=row.name,
        phone=row.phone,
        masked_phone=mask(row.phone),
        categories=row.categories,
        enabled=row.enabled,
        deleted=row.deleted,
        version=row.version,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def recipient_audit(row):
    # Raw phone/name are intentionally excluded from audit and general business responses.
    return {
        "version": row.version,
        "enabled": row.enabled,
        "deleted": row.deleted,
        "categories": row.categories,
    }


@router.get("/preferences", response_model=PreferenceList)
def preferences(user: SnapshotActor, db: SnapshotDB, response: Response):
    manager(user, db)
    rows = {row.category: row for row in db.scalars(select(AlertNotificationPreference))}
    response.headers["Cache-Control"] = "no-store"
    return PreferenceList(
        items=[preference_output(rows.get(category), category) for category in CATEGORIES],
        sms_mode=settings.sms_mode,
        notice="未配置时沿用规则的站内开关，短信默认关闭；仅明确分类的规则生成路由，配置本身不产生告警。本环境仅保留模拟或通道禁用回执，不发送真实短信。",
    )


@router.put("/preferences/{category}", response_model=PreferenceResponse)
def set_preference(
    category: Category,
    body: PreferenceInput,
    request: Request,
    user: Actor,
    db: DB,
    response: Response,
):
    authorize(request, user, db)
    row = db.get(AlertNotificationPreference, category, populate_existing=True)
    if (row.version if row else 0) != body.version:
        raise HTTPException(409, "通知配置已变化，请刷新核对；未覆盖他人修改")
    before = preference_output(row, category).model_dump(mode="json")
    if not row:
        row = AlertNotificationPreference(
            category=category,
            version=1,
            in_app_enabled=body.in_app_enabled,
            sms_enabled=body.sms_enabled,
            updated_by=user.id,
            updated_at=now(),
        )
        db.add(row)
    else:
        row.version += 1
        row.in_app_enabled = body.in_app_enabled
        row.sms_enabled = body.sms_enabled
        row.updated_by = user.id
        row.updated_at = now()
    result = preference_output(row, category)
    db.add(
        Audit(
            user_id=user.id,
            action="alerts.notification.preference",
            detail={"before": before, "after": result.model_dump(mode="json")},
        )
    )
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/recipients", response_model=RecipientPage)
def recipients(
    user: SnapshotActor,
    db: SnapshotDB,
    response: Response,
    q: str = Query("", max_length=80),
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    manager(user, db)
    stmt = select(AlertRecipient).where(AlertRecipient.deleted.is_(False))
    if q:
        stmt = stmt.where(
            AlertRecipient.name.contains(q, autoescape=True)
            | AlertRecipient.phone.contains(q, autoescape=True)
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(
        stmt.order_by(AlertRecipient.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    response.headers["Cache-Control"] = "no-store"
    return RecipientPage(
        items=[recipient_output(row) for row in rows], total=total, page=page, page_size=page_size
    )


@router.post("/recipients", response_model=RecipientResponse, status_code=201)
def create_recipient(
    body: RecipientInput, request: Request, user: Actor, db: DB, response: Response
):
    authorize(request, user, db)
    if (
        db.scalar(
            select(func.count())
            .select_from(AlertRecipient)
            .where(AlertRecipient.deleted.is_(False))
        )
        >= 500
    ):
        raise HTTPException(422, "最多维护500个未删除接收人，请先整理名单")
    row = AlertRecipient(
        **body.model_dump(), created_by=user.id, updated_by=user.id, version=1, deleted=False
    )
    db.add(row)
    try:
        db.flush()
        result = recipient_output(row)
        db.add(
            Audit(
                user_id=user.id,
                action="alerts.notification.recipient.create",
                object_id=row.id,
                detail={"after": recipient_audit(row)},
            )
        )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409, "该手机号已有有效接收人，未重复创建") from error
    response.headers["Cache-Control"] = "no-store"
    return result


def locked_recipient(db, recipient_id, version):
    row = db.scalar(
        select(AlertRecipient)
        .where(AlertRecipient.id == recipient_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not row or row.deleted:
        raise HTTPException(404, "接收人不存在或已删除")
    if row.version != version:
        raise HTTPException(409, "接收人已变化，请刷新核对版本")
    return row


@router.patch("/recipients/{recipient_id}", response_model=RecipientResponse)
def update_recipient(
    recipient_id: int,
    body: RecipientUpdate,
    request: Request,
    user: Actor,
    db: DB,
    response: Response,
):
    authorize(request, user, db)
    row = locked_recipient(db, recipient_id, body.version)
    before = recipient_audit(row)
    phone_changed = row.phone != body.phone
    for key, value in body.model_dump(exclude={"version"}).items():
        setattr(row, key, value)
    row.version += 1
    row.updated_by = user.id
    row.updated_at = now()
    try:
        db.flush()
        result = recipient_output(row)
        db.add(
            Audit(
                user_id=user.id,
                action="alerts.notification.recipient.update",
                object_id=row.id,
                detail={
                    "before": before,
                    "after": recipient_audit(row),
                    "phone_changed": phone_changed,
                },
            )
        )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(409, "手机号与其他有效接收人冲突，修改未保存") from error
    response.headers["Cache-Control"] = "no-store"
    return result


@router.delete("/recipients/{recipient_id}", response_model=RecipientResponse)
def delete_recipient(
    recipient_id: int,
    request: Request,
    user: Actor,
    db: DB,
    response: Response,
    version: int = Query(..., ge=1, le=2147483647),
):
    authorize(request, user, db)
    row = locked_recipient(db, recipient_id, version)
    before = recipient_audit(row)
    row.deleted = True
    row.enabled = False
    row.version += 1
    row.updated_by = user.id
    row.updated_at = now()
    result = recipient_output(row)
    db.add(
        Audit(
            user_id=user.id,
            action="alerts.notification.recipient.delete",
            object_id=row.id,
            detail={"before": before, "after": recipient_audit(row)},
        )
    )
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/routings", response_model=RoutingPage)
def routings(
    user: SnapshotActor,
    db: SnapshotDB,
    response: Response,
    event_id: int | None = Query(None, ge=1, le=2147483647),
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    manager(user, db)
    stmt = select(AlertNotificationRouting)
    if event_id is not None:
        stmt = stmt.where(AlertNotificationRouting.event_id == event_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(
        stmt.order_by(AlertNotificationRouting.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    response.headers["Cache-Control"] = "no-store"
    return RoutingPage(
        items=[
            RoutingResponse(
                id=r.id,
                event_id=r.event_id,
                phase=r.phase,
                category=r.category,
                preference_version=r.preference_version,
                in_app_enabled=r.in_app_enabled,
                sms_enabled=r.sms_enabled,
                sms_status=r.sms_status,
                notice_id=r.notice_id,
                created_at=r.created_at.isoformat(),
            )
            for r in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/deliveries", response_model=DeliveryPage)
def deliveries(
    user: SnapshotActor,
    db: SnapshotDB,
    response: Response,
    event_id: int | None = Query(None, ge=1, le=2147483647),
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(20, ge=1, le=100),
):
    manager(user, db)
    stmt = select(AlertSmsDelivery, AlertNotificationRouting).join(
        AlertNotificationRouting, AlertNotificationRouting.id == AlertSmsDelivery.routing_id
    )
    if event_id is not None:
        stmt = stmt.where(AlertNotificationRouting.event_id == event_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(
        stmt.order_by(AlertSmsDelivery.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    response.headers["Cache-Control"] = "no-store"
    return DeliveryPage(
        items=[
            DeliveryResponse(
                id=d.id,
                routing_id=r.id,
                event_id=r.event_id,
                phase=r.phase,
                category=r.category,
                recipient_id=d.recipient_id,
                recipient_version=d.recipient_version,
                recipient_name=d.name_snapshot,
                masked_phone=mask(d.phone_snapshot),
                status=d.status,
                created_at=d.created_at.isoformat(),
            )
            for d, r in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )
