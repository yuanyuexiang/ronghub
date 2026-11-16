"""Allowlisted order ingestion: no driver, vehicle, phone or payment credential data."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import or_, select

from app.models import ChargingOrder, Operator, Station, now
from app.scopes import station_scope


class SourceOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_id: str = Field(min_length=1, max_length=100)
    equipment_external_id: str | None = Field(None, min_length=1, max_length=100)
    connector_external_id: str | None = Field(None, min_length=1, max_length=100)
    status: Literal["unknown", "charging", "completed", "interrupted", "cancelled"]
    started_at: datetime
    ended_at: datetime | None = None
    duration_seconds: int | None = Field(None, ge=0, le=2147483647, strict=True)
    kwh: Decimal | None = Field(None, ge=0, max_digits=18, decimal_places=6)
    amount: Decimal | None = Field(None, ge=0, max_digits=18, decimal_places=2)
    currency: Literal["CNY"] | None = None
    source_updated_at: datetime

    @model_validator(mode="after")
    def coherent(self):
        for stamp in [self.started_at, self.ended_at, self.source_updated_at]:
            if stamp is not None and stamp.utcoffset() is None:
                raise ValueError("Order timestamps must include timezone")
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("Order end precedes start")
        if self.status == "completed" and self.ended_at is None:
            raise ValueError("Completed order requires an end time")
        if self.status == "charging" and self.ended_at is not None:
            raise ValueError("Charging order cannot have an end time")
        if self.connector_external_id and not self.equipment_external_id:
            raise ValueError("Connector reference requires equipment reference")
        if self.amount is not None and self.currency != "CNY":
            raise ValueError("Reported amount requires explicit CNY currency")
        if not self.external_id.strip():
            raise ValueError("Order reference cannot be blank")
        return self


def ingest(
    db, source: str, operator_id: int, station: Station, records: list[dict], *, is_demo: bool
):
    # Shared with station mutations and sync, including independent adapter callers.
    station = db.scalar(select(Station).where(Station.id == station.id).with_for_update())
    if not station or station.operator_id != operator_id or station.source != source:
        raise ValueError("Order station ownership/source mismatch")
    changed = stale = 0
    for raw in records:
        data = SourceOrder.model_validate(raw)
        values = data.model_dump()
        existing = db.scalar(
            select(ChargingOrder)
            .where(
                ChargingOrder.source == source,
                ChargingOrder.operator_id == operator_id,
                ChargingOrder.external_id == data.external_id,
            )
            .with_for_update()
        )
        if existing:
            if existing.station_id != station.id:
                raise ValueError("Order cannot move between stations")
            if data.source_updated_at < existing.source_updated_at:
                stale += 1
                continue
            if data.source_updated_at == existing.source_updated_at:
                if (
                    any(getattr(existing, key) != value for key, value in values.items())
                    or existing.is_demo != is_demo
                ):
                    raise ValueError("Same order source revision has conflicting values")
                continue
            for key, value in values.items():
                setattr(existing, key, value)
            existing.version += 1
            existing.synced_at = now()
            existing.is_demo = is_demo
        else:
            db.add(
                ChargingOrder(
                    station_id=station.id,
                    operator_id=operator_id,
                    source=source,
                    is_demo=is_demo,
                    **values,
                )
            )
        db.flush()
        changed += 1
    return {"received": len(records), "changed": changed, "ignored_stale": stale}


def statement(user, params):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "无权查看订单数据")
    stmt = station_scope(
        select(
            ChargingOrder,
            Station.name.label("station_name"),
            Operator.name.label("operator_name"),
            Station.district,
        )
        .join(Station, Station.id == ChargingOrder.station_id)
        .join(Operator, Operator.id == ChargingOrder.operator_id),
        user,
    )
    # A stale or corrupt station/operator reference cannot expose another enterprise's order.
    stmt = stmt.where(ChargingOrder.operator_id == Station.operator_id)
    if user.role == "operator":
        stmt = stmt.where(ChargingOrder.operator_id == user.operator_id)
    if params["q"].strip():
        stmt = stmt.where(
            or_(
                ChargingOrder.external_id.icontains(params["q"].strip(), autoescape=True),
                Station.name.icontains(params["q"].strip(), autoescape=True),
                Operator.name.icontains(params["q"].strip(), autoescape=True),
            )
        )
    for key in ["station_id", "operator_id", "equipment_external_id", "status", "source"]:
        if params[key] is not None and params[key] != "":
            stmt = stmt.where(getattr(ChargingOrder, key) == params[key])
    if params["start_at"]:
        stmt = stmt.where(ChargingOrder.started_at >= params["start_at"])
    if params["end_at"]:
        stmt = stmt.where(ChargingOrder.started_at < params["end_at"])
    return stmt
