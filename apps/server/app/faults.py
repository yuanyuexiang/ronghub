"""Allowlisted source fault events; no inferred availability or repair duration."""

from datetime import datetime
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import or_, select

from app.models import FaultEvent, Operator, Station, now
from app.scopes import station_scope

NOTICE = "按故障发生时间筛选源报事件；源状态与人工告警确认分离。恢复经过时长仅由源报发生/恢复时间相减，不代表维修工时；进行中和未知事件不计入平均值。按来源/演示标识分别统计，数据完整性未核实，不推导在线率、故障率或利用率。"


class SourceFaultEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_id: str = Field(min_length=1, max_length=100)
    equipment_external_id: str | None = Field(None, min_length=1, max_length=100)
    connector_external_id: str | None = Field(None, min_length=1, max_length=100)
    fault_code: str | None = Field(None, min_length=1, max_length=100)
    status: Literal["unknown", "active", "recovered"]
    severity: Literal["unknown", "info", "warning", "critical"] = "unknown"
    started_at: datetime
    recovered_at: datetime | None = None
    source_updated_at: datetime

    @model_validator(mode="after")
    def coherent(self):
        for stamp in [self.started_at, self.recovered_at, self.source_updated_at]:
            if stamp is not None and stamp.utcoffset() is None:
                raise ValueError("Fault timestamps require timezone")
        if (self.status == "recovered") != (self.recovered_at is not None):
            raise ValueError("Only recovered faults require a recovery timestamp")
        if self.recovered_at is not None and self.recovered_at < self.started_at:
            raise ValueError("Recovery precedes fault start")
        for value in [
            self.external_id,
            self.equipment_external_id,
            self.connector_external_id,
            self.fault_code,
        ]:
            if value is not None and not value.strip():
                raise ValueError("Source identifiers must not be blank")
        if self.connector_external_id is not None and self.equipment_external_id is None:
            raise ValueError("Connector reference requires equipment reference")
        return self


def ingest(db, source, operator_id, station, records, *, is_demo):
    station = db.scalar(select(Station).where(Station.id == station.id).with_for_update())
    if not station or station.source != source or station.operator_id != operator_id:
        raise ValueError("Fault station ownership/source mismatch")
    changed = stale = 0
    for raw in records:
        item = SourceFaultEvent.model_validate(raw)
        values = item.model_dump()
        row = db.scalar(
            select(FaultEvent)
            .where(
                FaultEvent.source == source,
                FaultEvent.operator_id == operator_id,
                FaultEvent.external_id == item.external_id,
            )
            .with_for_update()
        )
        if row:
            if row.station_id != station.id:
                raise ValueError("Fault cannot move across stations")
            if item.source_updated_at < row.source_updated_at:
                stale += 1
                continue
            if item.source_updated_at == row.source_updated_at:
                if (
                    any(getattr(row, key) != value for key, value in values.items())
                    or row.is_demo != is_demo
                ):
                    raise ValueError("Same fault source revision has conflicting values")
                continue
            for key, value in values.items():
                setattr(row, key, value)
            row.version += 1
            row.is_demo, row.synced_at = is_demo, now()
        else:
            db.add(
                FaultEvent(
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
        raise HTTPException(403, "无权查看故障事件")
    stmt = station_scope(
        select(
            FaultEvent,
            Station.name.label("station_name"),
            Operator.name.label("operator_name"),
            Station.district,
        )
        .join(Station, Station.id == FaultEvent.station_id)
        .join(Operator, Operator.id == FaultEvent.operator_id),
        user,
    ).where(FaultEvent.operator_id == Station.operator_id)
    if params["q"].strip():
        stmt = stmt.where(
            or_(
                *[
                    column.icontains(params["q"].strip(), autoescape=True)
                    for column in [
                        FaultEvent.external_id,
                        FaultEvent.fault_code,
                        Station.name,
                        Operator.name,
                    ]
                ]
            )
        )
    for key in [
        "station_id",
        "operator_id",
        "source",
        "equipment_external_id",
        "connector_external_id",
        "status",
        "severity",
    ]:
        if params[key] is not None and params[key] != "":
            stmt = stmt.where(getattr(FaultEvent, key) == params[key])
    if params["start_at"]:
        stmt = stmt.where(FaultEvent.started_at >= params["start_at"])
    if params["end_at"]:
        stmt = stmt.where(FaultEvent.started_at < params["end_at"])
    return stmt
