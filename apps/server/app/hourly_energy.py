"""Allowlisted source hourly intervals, distinct from daily energy and source orders."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from app.models import HourlyEnergyRecord, Station, now

BEIJING = ZoneInfo("Asia/Shanghai")


def aligned(stamp):
    if stamp.utcoffset() is None:
        return False
    local = stamp.astimezone(BEIJING)
    return local.minute == 0 and local.second == 0 and local.microsecond == 0


class SourceHourlyEnergy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    meter_scope: Literal["station", "equipment"]
    equipment_external_id: str = Field("", max_length=100)
    started_at: datetime
    ended_at: datetime
    kwh: Decimal | None = Field(None, ge=0, max_digits=18, decimal_places=6)
    source_updated_at: datetime

    @model_validator(mode="after")
    def coherent(self):
        if (
            not aligned(self.started_at)
            or self.ended_at.utcoffset() is None
            or self.source_updated_at.utcoffset() is None
        ):
            raise ValueError("Hourly timestamps must include timezone and align to a Beijing hour")
        if self.ended_at.astimezone(UTC) - self.started_at.astimezone(UTC) != timedelta(hours=1):
            raise ValueError("Hourly energy must cover exactly one hour")
        if (self.meter_scope == "station" and self.equipment_external_id) or (
            self.meter_scope == "equipment" and not self.equipment_external_id.strip()
        ):
            raise ValueError("Meter scope and equipment reference disagree")
        return self


def ingest(db, source, operator_id, station, records, *, is_demo):
    station = db.scalar(select(Station).where(Station.id == station.id).with_for_update())
    if not station or station.source != source or station.operator_id != operator_id:
        raise ValueError("Hourly station ownership/source mismatch")
    changed = stale = 0
    for raw in records:
        item = SourceHourlyEnergy.model_validate(raw)
        values = item.model_dump()
        row = db.scalar(
            select(HourlyEnergyRecord)
            .where(
                HourlyEnergyRecord.station_id == station.id,
                HourlyEnergyRecord.operator_id == operator_id,
                HourlyEnergyRecord.source == source,
                HourlyEnergyRecord.meter_scope == item.meter_scope,
                HourlyEnergyRecord.equipment_external_id == item.equipment_external_id,
                HourlyEnergyRecord.started_at == item.started_at,
            )
            .with_for_update()
        )
        if row:
            if item.source_updated_at < row.source_updated_at:
                stale += 1
                continue
            if item.source_updated_at == row.source_updated_at:
                if (
                    any(getattr(row, key) != value for key, value in values.items())
                    or row.is_demo != is_demo
                ):
                    raise ValueError("Same hourly source revision has conflicting values")
                continue
            for key, value in values.items():
                setattr(row, key, value)
            row.version += 1
            row.is_demo, row.synced_at = is_demo, now()
        else:
            db.add(
                HourlyEnergyRecord(
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
