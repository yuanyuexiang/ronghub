"""Allowlisted finite observations, independent connectivity and operation axes."""

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select, text

from app.models import RuntimeInterval, Station, now
from app.scopes import station_scope

STATES = {
    "connectivity": ["online", "offline", "unknown"],
    "operation": ["available", "occupied", "charging", "faulted", "maintenance", "unknown"],
}
NOTICE = "同一来源及演示标识内，分别查看连接状态与业务状态，不将故障等同于离线。只计算来源明确上送的有限区间，并裁切到开始含/结束不含的查询窗口；明确未知与未上送时长分开。占比以该维度已上送时长（含未知）为分母，无样本时为空。查询窗口不是营业/可用时长，来源完整性未核实，不代表正式在线率、故障率或利用率；源桩枪编码尚未认证为设备映射。"


class SourceRuntimeInterval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_id: str = Field(min_length=1, max_length=100)
    equipment_external_id: str = Field(min_length=1, max_length=100)
    connector_external_id: str = Field("", max_length=100)
    dimension: Literal["connectivity", "operation"]
    state: str = Field(max_length=20)
    started_at: datetime
    ended_at: datetime
    source_updated_at: datetime

    @model_validator(mode="after")
    def coherent(self):
        if any(
            stamp.utcoffset() is None
            for stamp in [self.started_at, self.ended_at, self.source_updated_at]
        ):
            raise ValueError("Runtime interval timestamps require timezone")
        if self.ended_at > self.source_updated_at:
            raise ValueError("Observed interval must end no later than source revision time")
        if self.ended_at <= self.started_at:
            raise ValueError("Runtime interval end must follow start")
        if self.state not in STATES[self.dimension]:
            raise ValueError("State does not belong to runtime dimension")
        if (
            not self.external_id.strip()
            or not self.equipment_external_id.strip()
            or (self.connector_external_id and not self.connector_external_id.strip())
        ):
            raise ValueError("Runtime references must not be blank")
        return self


def ingest(db, source, operator_id, station, records, *, is_demo):
    station = db.scalar(select(Station).where(Station.id == station.id).with_for_update())
    if not station or station.source != source or station.operator_id != operator_id:
        raise ValueError("Runtime station ownership/source mismatch")
    # Adjacent boundaries may be corrected together; validate the final batch, not intermediate rows.
    db.execute(text("SET CONSTRAINTS ex_runtime_no_overlap DEFERRED"))
    changed = stale = 0
    for raw in records:
        item = SourceRuntimeInterval.model_validate(raw)
        values = item.model_dump()
        row = db.scalar(
            select(RuntimeInterval)
            .where(
                RuntimeInterval.source == source,
                RuntimeInterval.operator_id == operator_id,
                RuntimeInterval.external_id == item.external_id,
            )
            .with_for_update()
        )
        if row:
            if row.station_id != station.id or any(
                getattr(row, key) != getattr(item, key)
                for key in ["equipment_external_id", "connector_external_id", "dimension"]
            ):
                raise ValueError("Runtime interval cannot move across objects or dimensions")
            if item.source_updated_at < row.source_updated_at:
                stale += 1
                continue
            if item.source_updated_at == row.source_updated_at:
                if (
                    any(getattr(row, key) != value for key, value in values.items())
                    or row.is_demo != is_demo
                ):
                    raise ValueError("Same runtime source revision has conflicting values")
                continue
            for key, value in values.items():
                setattr(row, key, value)
            row.version += 1
            row.is_demo, row.synced_at = is_demo, now()
        else:
            db.add(
                RuntimeInterval(
                    station_id=station.id,
                    operator_id=operator_id,
                    source=source,
                    is_demo=is_demo,
                    **values,
                )
            )
        db.flush()
        changed += 1
    db.execute(text("SET CONSTRAINTS ex_runtime_no_overlap IMMEDIATE"))
    return {"received": len(records), "changed": changed, "ignored_stale": stale}


def station_for(db, user, params):
    if user.role not in {"regulator", "operator"}:
        raise HTTPException(403, "无权查看历史运行状态")
    station = db.scalar(
        station_scope(select(Station), user).where(Station.id == params["station_id"])
    )
    if not station:
        raise HTTPException(404, "场站不存在或不可访问")
    return station


def statement(station, params):
    return select(RuntimeInterval).where(
        RuntimeInterval.station_id == station.id,
        RuntimeInterval.operator_id == station.operator_id,
        RuntimeInterval.source == params["source"],
        RuntimeInterval.equipment_external_id == params["equipment_external_id"],
        RuntimeInterval.connector_external_id == params["connector_external_id"],
        RuntimeInterval.started_at < params["end_at"],
        RuntimeInterval.ended_at > params["start_at"],
    )


def seconds(delta):
    return Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / 1000000


def output(row, params):
    start, end = max(row.started_at, params["start_at"]), min(row.ended_at, params["end_at"])
    return {
        **{
            key: getattr(row, key)
            for key in ["id", "external_id", "dimension", "state", "is_demo", "version"]
        },
        **{
            key: getattr(row, key).isoformat()
            for key in ["started_at", "ended_at", "source_updated_at", "synced_at"]
        },
        "clipped_start_at": start.isoformat(),
        "clipped_end_at": end.isoformat(),
        "observed_seconds": str(seconds(end - start)),
    }


def report(db, station, params, page, page_size):
    stmt = statement(station, params)
    base = stmt.subquery()
    clipped = func.extract(
        "epoch",
        func.least(base.c.ended_at, params["end_at"])
        - func.greatest(base.c.started_at, params["start_at"]),
    )
    groups = list(
        db.execute(
            select(
                base.c.is_demo,
                base.c.dimension,
                base.c.state,
                func.sum(clipped).label("seconds"),
                func.count().label("records"),
            ).group_by(base.c.is_demo, base.c.dimension, base.c.state)
        )
    )
    window = seconds(params["end_at"] - params["start_at"])
    streams = []
    for demo in sorted({row.is_demo for row in groups}):
        axes = build_axes([row for row in groups if row.is_demo == demo], window)
        streams.append({"is_demo": demo, "axes": axes})
    total = db.scalar(select(func.count()).select_from(base))
    rows = db.scalars(
        stmt.order_by(RuntimeInterval.started_at, RuntimeInterval.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "station_id": station.id,
        "station_name": station.name,
        **{
            key: params[key] for key in ["source", "equipment_external_id", "connector_external_id"]
        },
        "start_at": params["start_at"].isoformat(),
        "end_at": params["end_at"].isoformat(),
        "window_seconds": str(window),
        "streams": streams,
        "items": [output(row, params) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "source_completeness_verified": False,
        "notice": NOTICE,
    }


def build_axes(groups, window):
    axes = []
    for dimension, states in STATES.items():
        matching = {row.state: row for row in groups if row.dimension == dimension}
        reported = sum((row.seconds for row in matching.values()), Decimal(0))
        unknown = matching["unknown"].seconds if "unknown" in matching else Decimal(0)
        axes.append(
            {
                "dimension": dimension,
                "reported_seconds": str(reported),
                "known_seconds": str(reported - unknown),
                "unknown_seconds": str(unknown),
                "unreported_seconds": str(window - reported),
                "states": [
                    {
                        "state": state,
                        "observed_seconds": str(
                            matching[state].seconds if state in matching else Decimal(0)
                        ),
                        "share_of_reported_pct": str(
                            (
                                (matching[state].seconds if state in matching else Decimal(0))
                                * 100
                                / reported
                            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        )
                        if reported
                        else None,
                    }
                    for state in states
                ],
            }
        )
    return axes
