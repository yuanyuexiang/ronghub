"""Explicit source-declared device cohorts; no historical inference from current inventory."""

from datetime import date, datetime, time, timedelta

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator
from sqlalchemy import select

from app.hourly_energy import BEIJING
from app.models import Equipment, Station, SubsidyDeviceScope, now


class SourceDeviceScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_id: str = Field(min_length=1, max_length=100)
    day: date
    equipment_external_ids: list[str] = Field(max_length=1000)
    complete: StrictBool
    source_updated_at: datetime

    @model_validator(mode="after")
    def coherent(self):
        refs = [self.external_id, *self.equipment_external_ids]
        if any(not ref.strip() or len(ref) > 100 or "\x00" in ref for ref in refs) or len(
            set(self.equipment_external_ids)
        ) != len(self.equipment_external_ids):
            raise ValueError("Cohort references must be bounded, nonblank and unique")
        if self.source_updated_at.utcoffset() is None or self.source_updated_at > now() + timedelta(
            minutes=5
        ):
            raise ValueError("Cohort revision requires a nonfuture timezone timestamp")
        if self.complete and self.source_updated_at < datetime.combine(
            self.day + timedelta(days=1), time.min, BEIJING
        ):
            raise ValueError("Complete cohort requires a closed Beijing day")
        self.equipment_external_ids = sorted(self.equipment_external_ids)
        return self


def ingest(db, source, operator_id, station, records, *, is_demo):
    if len(records) > 366:
        raise ValueError("Cohort batch exceeds 366 days")
    station = db.scalar(
        select(Station)
        .where(Station.id == station.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not station or station.source != source or station.operator_id != operator_id:
        raise ValueError("Cohort source ownership mismatch")
    known = set(db.scalars(select(Equipment.external_id).where(Equipment.station_id == station.id)))
    changed = stale = 0
    for raw in records:
        item = SourceDeviceScope.model_validate(raw)
        if not set(item.equipment_external_ids).issubset(known):
            raise ValueError("Cohort declares unmapped source equipment")
        row = db.scalar(
            select(SubsidyDeviceScope)
            .where(
                SubsidyDeviceScope.source == source,
                SubsidyDeviceScope.operator_id == operator_id,
                SubsidyDeviceScope.external_id == item.external_id,
            )
            .with_for_update()
        )
        values = item.model_dump()
        if row:
            if row.station_id != station.id or row.day != item.day or row.is_demo != is_demo:
                raise ValueError("Cohort cannot move station/day/mode")
            if item.source_updated_at < row.source_updated_at:
                stale += 1
                continue
            if item.source_updated_at == row.source_updated_at:
                if any(getattr(row, key) != value for key, value in values.items()):
                    raise ValueError("Conflicting cohort source revision")
                continue
            for key, value in values.items():
                setattr(row, key, value)
            row.version += 1
            row.synced_at = now()
        else:
            db.add(
                SubsidyDeviceScope(
                    source=source,
                    operator_id=operator_id,
                    station_id=station.id,
                    is_demo=is_demo,
                    **values,
                )
            )
        db.flush()
        changed += 1
    return {"received": len(records), "changed": changed, "ignored_stale": stale}
