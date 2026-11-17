"""Bounded source device/connector inventory; separate from runtime history."""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import case, func, or_, select

from app.contracts import ConnectorItem
from app.dependencies import SnapshotActor, scope
from app.device_metadata import STATES, TYPES, EquipmentType, connector_profile, equipment_profile
from app.models import Connector, Equipment, Station
from app.reporting import SnapshotDB

router = APIRouter(prefix="/api/v1/stations", tags=["device-inventory"])


class InventoryDevice(BaseModel):
    id: int
    external_id: str
    name: str
    power_kw: str
    equipment_type: EquipmentType | None
    model_name: str | None
    source_unique_code: str | None
    manufactured_on: str | None
    connector_count: int
    matching_connectors: int


class InventoryDevicePage(BaseModel):
    items: list[InventoryDevice]
    total: int
    page: int
    page_size: int
    station_equipment_count: int
    station_connector_count: int
    matching_connector_count: int
    equipment_type_options: dict[str, str]
    connector_status_options: dict[str, str]
    notice: str


class InventoryConnectorPage(BaseModel):
    items: list[ConnectorItem]
    total: int
    inventory_total: int
    page: int
    page_size: int
    equipment_id: int
    station_id: int


def station_access(db, user, station_id):
    if not db.scalar(scope(select(Station.id).where(Station.id == station_id), user)):
        raise HTTPException(404, "场站不存在或不可访问")


@router.get("/{station_id}/equipment/query", response_model=InventoryDevicePage)
def query(
    station_id: int,
    user: SnapshotActor,
    db: SnapshotDB,
    q: str = Query("", max_length=150),
    equipment_type: str = Query("", max_length=20),
    connector_status: str = Query("", max_length=40),
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(10, ge=1, le=100),
):
    station_access(db, user, station_id)
    if equipment_type and equipment_type not in {*TYPES, "unknown"}:
        raise HTTPException(422, "不支持的设备类型")
    stmt = select(Equipment).where(Equipment.station_id == station_id)
    if q:
        stmt = stmt.where(
            or_(
                Equipment.external_id.icontains(q, autoescape=True),
                Equipment.name.icontains(q, autoescape=True),
                Equipment.source_unique_code.icontains(q, autoescape=True),
            )
        )
    if equipment_type:
        stmt = stmt.where(
            Equipment.equipment_type.is_(None)
            if equipment_type == "unknown"
            else Equipment.equipment_type == equipment_type
        )
    if connector_status:
        stmt = stmt.where(
            select(Connector.id)
            .where(Connector.equipment_id == Equipment.id, Connector.status == connector_status)
            .exists()
        )
    ids = stmt.with_only_columns(Equipment.id).subquery()
    total = db.scalar(select(func.count()).select_from(ids))
    rows = list(
        db.scalars(stmt.order_by(Equipment.id).offset((page - 1) * page_size).limit(page_size))
    )
    inventory_count = db.scalar(
        select(func.count()).select_from(Equipment).where(Equipment.station_id == station_id)
    )
    station_connectors = db.scalar(
        select(func.count())
        .select_from(Connector)
        .join(Equipment, Equipment.id == Connector.equipment_id)
        .where(Equipment.station_id == station_id)
    )
    matching_stmt = (
        select(func.count())
        .select_from(Connector)
        .where(Connector.equipment_id.in_(select(ids.c.id)))
    )
    if connector_status:
        matching_stmt = matching_stmt.where(Connector.status == connector_status)
    matching_connectors = db.scalar(matching_stmt)
    counts = {}
    if rows:
        for eid, count, matches in db.execute(
            select(
                Connector.equipment_id,
                func.count(),
                func.sum(case((Connector.status == connector_status, 1), else_=0)),
            )
            .where(Connector.equipment_id.in_([r.id for r in rows]))
            .group_by(Connector.equipment_id)
        ):
            counts[eid] = (count, matches if connector_status else count)
    return {
        "items": [
            {
                **equipment_profile(r),
                "connector_count": counts.get(r.id, (0, 0))[0],
                "matching_connectors": counts.get(r.id, (0, 0))[1],
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "station_equipment_count": inventory_count,
        "station_connector_count": station_connectors,
        "matching_connector_count": matching_connectors,
        "equipment_type_options": TYPES,
        "connector_status_options": STATES,
        "notice": "当前来源台账与终端状态快照，非运行历史或完整来源认证。设备编码搜索包含来源编码、名称及来源上报唯一编码；状态筛选匹配终端并保留所属设备。未知资料不从功率或编码推测。",
    }


@router.get(
    "/{station_id}/equipment/{equipment_id}/connectors", response_model=InventoryConnectorPage
)
def connectors(
    station_id: int,
    equipment_id: int,
    user: SnapshotActor,
    db: SnapshotDB,
    q: str = Query("", max_length=150),
    connector_status: str = Query("", max_length=40),
    page: int = Query(1, ge=1, le=2147483647),
    page_size: int = Query(10, ge=1, le=100),
):
    station_access(db, user, station_id)
    if not db.scalar(
        select(Equipment.id).where(Equipment.station_id == station_id, Equipment.id == equipment_id)
    ):
        raise HTTPException(404, "设备不存在或不属于当前场站")
    stmt = select(Connector).where(Connector.equipment_id == equipment_id)
    if q:
        stmt = stmt.where(
            or_(
                Connector.external_id.icontains(q, autoescape=True),
                Connector.name.icontains(q, autoescape=True),
            )
        )
    if connector_status:
        stmt = stmt.where(Connector.status == connector_status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    inventory = db.scalar(
        select(func.count()).select_from(Connector).where(Connector.equipment_id == equipment_id)
    )
    rows = db.scalars(stmt.order_by(Connector.id).offset((page - 1) * page_size).limit(page_size))
    return {
        "items": [connector_profile(r) for r in rows],
        "total": total,
        "inventory_total": inventory,
        "page": page,
        "page_size": page_size,
        "equipment_id": equipment_id,
        "station_id": station_id,
    }
