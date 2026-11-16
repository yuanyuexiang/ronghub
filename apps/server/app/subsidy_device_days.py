"""Independent source device-day observations; no order/runtime backfill or financial eligibility."""

from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator
from sqlalchemy import select

from app.hourly_energy import BEIJING
from app.models import Equipment, Station, SubsidyDeviceDay, SubsidyDeviceScope, now

Seconds = Annotated[Decimal, Field(ge=0, le=86400, max_digits=12, decimal_places=6)]
NOTICE = "独立设备日观测，仅列来源已上报记录，不以订单、站级或小时电量补写；不代表设备/日期完整覆盖。来源设备编码按同源场站台账精确匹配，尚非正式财务映射认证。日历日比例以北京时间完整一天86400秒为分母，仅该轴上报完整且无未知状态时显示，非已确认补贴政策率；缺失/部分覆盖不置零。本页仅列原始观测；金额须使用明确执行口径、完整设备集合和历史公示依据另行模拟核算，不代表正式结算。"


class SourceDeviceDay(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope_external_id: str | None = Field(None, min_length=1, max_length=100)
    external_id: str = Field(min_length=1, max_length=100)
    equipment_external_id: str = Field(min_length=1, max_length=100)
    day: date
    equipment_name: str | None = Field(None, min_length=1, max_length=150)
    equipment_type: Literal["dc", "ac", "swapping", "unknown"] = "unknown"
    power_kw: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    production_date: date | None = None
    business_kind: Literal["charging", "swapping", "unknown"]
    energy_kwh: Decimal | None = Field(None, ge=0, max_digits=18, decimal_places=6)
    energy_complete: StrictBool
    metric_basis: Literal["calendar_day_seconds_v1", "unconfirmed"]
    fault_observed_seconds: Seconds | None = None
    fault_unknown_seconds: Seconds | None = None
    fault_seconds: Seconds | None = None
    offline_observed_seconds: Seconds | None = None
    offline_unknown_seconds: Seconds | None = None
    offline_seconds: Seconds | None = None
    source_updated_at: datetime

    @model_validator(mode="after")
    def coherent(self):
        if any(
            not value.strip() or "\x00" in value
            for value in [
                self.external_id,
                self.equipment_external_id,
                *([self.equipment_name] if self.equipment_name is not None else []),
                *([self.scope_external_id] if self.scope_external_id is not None else []),
            ]
        ):
            raise ValueError("Source references and names must be nonblank and contain no NUL")
        if self.source_updated_at.utcoffset() is None or self.source_updated_at > now() + timedelta(
            minutes=5
        ):
            raise ValueError("Source revision needs timezone and cannot be in the future")
        start = datetime.combine(self.day, time.min, BEIJING)
        delta = self.source_updated_at - start
        elapsed = (
            Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / 1000000
        )
        if elapsed < 0 or self.production_date is not None and self.production_date > self.day:
            raise ValueError("Source revision/production date contradicts observation day")
        if self.energy_complete and (self.energy_kwh is None or elapsed < 86400):
            raise ValueError("Complete energy requires a value and a closed Beijing day")
        for axis in ["fault", "offline"]:
            observed, unknown, affected = (
                getattr(self, axis + "_" + part)
                for part in ["observed_seconds", "unknown_seconds", "seconds"]
            )
            values = [observed, unknown, affected]
            if all(value is None for value in values):
                continue
            if (
                any(value is None for value in values)
                or observed > min(Decimal(86400), elapsed)
                or unknown + affected > observed
            ):
                raise ValueError("Each axis needs coherent observed/unknown/affected seconds")
        return self


def ingest(db, source, operator_id, station, records, *, is_demo):
    if len(records) > 10000 or type(is_demo) is not bool:
        raise ValueError("Device-day batch limit or demonstration marker invalid")
    station = db.scalar(
        select(Station)
        .where(Station.id == station.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not station or station.source != source or station.operator_id != operator_id:
        raise ValueError("Device-day source/station ownership mismatch")
    items = [SourceDeviceDay.model_validate(raw) for raw in records]
    equipment = {
        row.external_id: row
        for row in db.scalars(
            select(Equipment).where(
                Equipment.station_id == station.id,
                Equipment.external_id.in_({item.equipment_external_id for item in items}),
            )
        )
    }
    existing = {
        row.external_id: row
        for row in db.scalars(
            select(SubsidyDeviceDay)
            .where(
                SubsidyDeviceDay.source == source,
                SubsidyDeviceDay.operator_id == operator_id,
                SubsidyDeviceDay.external_id.in_({item.external_id for item in items}),
            )
            .with_for_update()
        )
    }
    changed = stale = 0
    for item in items:
        mapped = equipment.get(item.equipment_external_id)
        if not mapped:
            raise ValueError("No exact device mapping inside source-owned station")
        values = item.model_dump()
        scope = (
            db.scalar(
                select(SubsidyDeviceScope).where(
                    SubsidyDeviceScope.station_id == station.id,
                    SubsidyDeviceScope.source == source,
                    SubsidyDeviceScope.external_id == item.scope_external_id,
                )
            )
            if item.scope_external_id
            else None
        )
        if item.scope_external_id and (
            not scope
            or scope.day != item.day
            or scope.is_demo != is_demo
            or item.equipment_external_id not in scope.equipment_external_ids
        ):
            raise ValueError("Device evidence lacks its exact source cohort membership")
        values["scope_id"] = scope.id if scope else None

        row = existing.get(item.external_id)
        if row:
            if (
                row.station_id != station.id
                or row.equipment_id != mapped.id
                or row.day != item.day
                or row.is_demo != is_demo
            ):
                raise ValueError("Source observation cannot move between devices/days/modes")
            if item.source_updated_at < row.source_updated_at:
                stale += 1
                continue
            if item.source_updated_at == row.source_updated_at:
                if any(getattr(row, key) != value for key, value in values.items()):
                    raise ValueError("Same source revision has conflicting device-day values")
                continue
            for key, value in values.items():
                setattr(row, key, value)
            row.version += 1
            row.synced_at = now()
        else:
            row = SubsidyDeviceDay(
                source=source,
                station_id=station.id,
                operator_id=operator_id,
                equipment_id=mapped.id,
                is_demo=is_demo,
                **values,
            )
            db.add(row)
            existing[item.external_id] = row
        db.flush()
        changed += 1
    return {"received": len(items), "changed": changed, "ignored_stale": stale}


def ratio(row, axis):
    observed = getattr(row, axis + "_observed_seconds")
    unknown = getattr(row, axis + "_unknown_seconds")
    if observed is None:
        return None, "missing"
    if row.metric_basis != "calendar_day_seconds_v1":
        return None, "basis_unconfirmed"
    if observed != 86400:
        return None, "partial"
    if unknown != 0:
        return None, "unknown_states"
    return str(
        (getattr(row, axis + "_seconds") * 100 / Decimal(86400)).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        )
    ), None
