"""Durable worker; mock adapter only, no undocumented customer endpoint calls."""

import argparse
import secrets
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from typing import Protocol

from sqlalchemy import and_, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert

from app import station_metadata
from app.config import settings
from app.db import SessionLocal
from app.models import Connector, Equipment, Station, SyncTask, now


class Adapter(Protocol):
    def fetch(self, operator_id: int) -> list[dict]: ...


class DemoAdapter:
    def fetch(self, operator_id: int) -> list[dict]:
        if not settings.demo_mode:
            raise ValueError("Demo adapter disabled")
        return [
            {
                "external_id": f"demo-{operator_id}",
                "name": "演示·接口同步场站",
                "district": "鼓楼区",
                "address": "模拟接口数据，非真实地址",
                "power_kw": "120.00",
                "station_type": "charging",
                "usage_type": "public",
                "construction_site": "building_parking",
                "site_state": "normal",
                "source_sync_state": "active",
                "source_first_connected_at": "2026-11-01T08:00:00+08:00",
                "longitude": "119.300000",
                "latitude": "26.080000",
                "coordinate_system": "wgs84",
                "location_is_demo": True,
                "runtime_intervals": [
                    {
                        "external_id": f"demo-runtime-{operator_id}-{index}",
                        "equipment_external_id": "equipment-1",
                        "connector_external_id": connector,
                        "dimension": dimension,
                        "state": state,
                        "started_at": f"2026-11-01T{start}+08:00",
                        "ended_at": f"2026-11-01T{end}+08:00",
                        "source_updated_at": "2026-11-01T12:05:00+08:00",
                    }
                    for index, (connector, dimension, state, start, end) in enumerate(
                        [
                            ("connector-1", "connectivity", "online", "08:00:00", "10:00:00"),
                            ("connector-1", "connectivity", "offline", "10:00:00", "11:00:00"),
                            ("connector-1", "connectivity", "unknown", "11:00:00", "11:30:00"),
                            ("connector-1", "operation", "charging", "08:00:00", "09:00:00"),
                            ("connector-1", "operation", "faulted", "09:00:00", "09:30:00"),
                            ("connector-1", "operation", "maintenance", "09:30:00", "10:00:00"),
                            ("connector-1", "operation", "unknown", "10:00:00", "10:30:00"),
                            ("connector-1", "operation", "available", "10:30:00", "11:00:00"),
                            ("", "connectivity", "online", "08:00:00", "12:00:00"),
                        ]
                    )
                ],
                "faults": [
                    {
                        "external_id": f"demo-fault-{operator_id}-1",
                        "equipment_external_id": "equipment-1",
                        "connector_external_id": "connector-1",
                        "fault_code": "DEMO-THERMAL",
                        "status": "recovered",
                        "severity": "warning",
                        "started_at": "2026-11-01T07:00:00+08:00",
                        "recovered_at": "2026-11-01T07:30:00+08:00",
                        "source_updated_at": "2026-11-01T11:10:00+08:00",
                    },
                    {
                        "external_id": f"demo-fault-{operator_id}-2",
                        "equipment_external_id": "equipment-1",
                        "connector_external_id": "connector-2",
                        "fault_code": "DEMO-COMMS",
                        "status": "active",
                        "severity": "critical",
                        "started_at": "2026-11-01T10:00:00+08:00",
                        "source_updated_at": "2026-11-01T11:10:00+08:00",
                    },
                ],
                "hourly_energy": [
                    {
                        "meter_scope": "station",
                        "started_at": "2026-11-01T08:00:00+08:00",
                        "ended_at": "2026-11-01T09:00:00+08:00",
                        "kwh": "8.125000",
                        "source_updated_at": "2026-11-01T11:05:00+08:00",
                    },
                    {
                        "meter_scope": "station",
                        "started_at": "2026-11-01T09:00:00+08:00",
                        "ended_at": "2026-11-01T10:00:00+08:00",
                        "kwh": None,
                        "source_updated_at": "2026-11-01T11:05:00+08:00",
                    },
                    {
                        "meter_scope": "station",
                        "started_at": "2026-11-01T10:00:00+08:00",
                        "ended_at": "2026-11-01T11:00:00+08:00",
                        "kwh": "0.000000",
                        "source_updated_at": "2026-11-01T11:05:00+08:00",
                    },
                ],
                "subsidy_device_scopes": [
                    {
                        "external_id": f"demo-scope-{operator_id}-{day}",
                        "day": f"2026-11-0{day}",
                        "equipment_external_ids": ["equipment-1"],
                        "complete": True,
                        "source_updated_at": "2026-11-05T01:00:00+08:00",
                    }
                    for day in [1, 2, 3]
                ],
                "subsidy_device_days": [
                    {
                        "external_id": f"demo-device-day-{operator_id}-{index}",
                        "scope_external_id": f"demo-scope-{operator_id}-{index}",
                        "equipment_external_id": "equipment-1",
                        "day": f"2026-11-0{index}",
                        "equipment_name": "演示直流设备",
                        "equipment_type": "dc",
                        "power_kw": "120.00",
                        "production_date": "2025-01-01",
                        "business_kind": "charging",
                        "energy_kwh": kwh,
                        "energy_complete": complete,
                        "metric_basis": "calendar_day_seconds_v1",
                        "fault_observed_seconds": observed,
                        "fault_unknown_seconds": "0",
                        "fault_seconds": "0",
                        "offline_observed_seconds": observed,
                        "offline_unknown_seconds": "0",
                        "offline_seconds": "0",
                        "source_updated_at": "2026-11-05T01:00:00+08:00",
                    }
                    for index, kwh, complete, observed in [
                        (1, "42.125000", True, "86400"),
                        (2, "0.000000", True, "86400"),
                        (3, None, False, "43200"),
                    ]
                ],
                "orders": [
                    {
                        "external_id": f"demo-order-{operator_id}-1",
                        "equipment_external_id": "equipment-1",
                        "connector_external_id": "connector-1",
                        "status": "completed",
                        "started_at": "2026-11-01T08:00:00+08:00",
                        "ended_at": "2026-11-01T09:00:00+08:00",
                        "duration_seconds": 3300,
                        "kwh": "42.005000",
                        "amount": "50.41",
                        "currency": "CNY",
                        "source_updated_at": "2026-11-01T09:05:00+08:00",
                    }
                ],
                "equipment": [
                    {
                        "external_id": "equipment-1",
                        "name": "演示直流设备",
                        "power_kw": "120.00",
                        "connectors": [
                            {"external_id": "connector-1", "status": "available"},
                            {"external_id": "connector-2", "status": "charging"},
                        ],
                    }
                ],
            }
        ]


def enqueue(db, operator_id: int, request_key: str):
    task_id = db.scalar(
        insert(SyncTask)
        .values(source="demo", operator_id=operator_id, request_key=request_key)
        .on_conflict_do_nothing(index_elements=["source", "request_key"])
        .returning(SyncTask.id)
    )
    if task_id:
        return db.get(SyncTask, task_id)
    task = db.scalar(
        select(SyncTask).where(SyncTask.source == "demo", SyncTask.request_key == request_key)
    )
    if task.operator_id != operator_id:
        raise ValueError("Idempotency key belongs to another operator")
    return task


def claim(task_id: int | None = None):
    token = secrets.token_urlsafe(24)
    with SessionLocal.begin() as db:
        # Exhausted tasks abandoned by crashed workers must not remain running forever.
        for t in db.scalars(
            select(SyncTask)
            .where(
                SyncTask.status == "running",
                SyncTask.lease_until <= now(),
                SyncTask.attempts >= SyncTask.max_attempts,
            )
            .with_for_update(skip_locked=True)
        ):
            t.status, t.error, t.lease_owner, t.lease_until = (
                "failed",
                "任务租约到期且超过重试次数",
                None,
                None,
            )
        task = db.scalar(
            select(SyncTask)
            .where(
                SyncTask.attempts < SyncTask.max_attempts,
                *([SyncTask.id == task_id] if task_id is not None else []),
                or_(
                    and_(SyncTask.status.in_(["pending", "retry"]), SyncTask.available_at <= now()),
                    and_(SyncTask.status == "running", SyncTask.lease_until <= now()),
                ),
            )
            .order_by(SyncTask.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not task:
            return None
        task.status, task.lease_owner = "running", token
        task.lease_until = now() + timedelta(seconds=120)
        task.attempts += 1
        return task.id, token, task.operator_id, task.source


def renew(task_id: int, token: str):
    with SessionLocal.begin() as db:
        # A renewal must not hang the heartbeat or revive an expired/reassigned lease.
        db.execute(text("SET LOCAL lock_timeout = '2s'"))
        db.execute(text("SET LOCAL statement_timeout = '5s'"))
        current = now()
        result = db.execute(
            update(SyncTask)
            .where(
                SyncTask.id == task_id,
                SyncTask.status == "running",
                SyncTask.lease_owner == token,
                SyncTask.lease_until > current,
            )
            .values(lease_until=current + timedelta(seconds=120))
        )
        return result.rowcount == 1


@contextmanager
def lease_heartbeat(task_id: int, token: str, interval: float = 30):
    stopped, lost = threading.Event(), threading.Event()

    def keep_alive():
        while not stopped.wait(interval):
            try:
                if not renew(task_id, token):
                    lost.set()
                    return
            except Exception:  # noqa: BLE001 - fail closed; caller must not commit stale results
                lost.set()
                return

    worker = threading.Thread(target=keep_alive, daemon=True, name="sync-lease-heartbeat")
    worker.start()
    try:
        yield lost
    finally:
        stopped.set()
        worker.join(timeout=6)
        if worker.is_alive():
            lost.set()


def ingest(db, source, operator_id, records):
    from app import (
        faults,
        hourly_energy,
        orders,
        runtime,
        subsidy_device_days,
        subsidy_device_scopes,
    )

    equipment_count = connector_count = 0
    order_count = hourly_count = fault_count = runtime_count = device_day_count = 0
    for record in records:
        # Explicit ownership and uniqueness: a source key cannot be reassigned across enterprises.
        existing = db.scalar(
            select(Station).where(
                Station.source == source, Station.external_id == record["external_id"]
            )
        )
        if existing and existing.operator_id != operator_id:
            raise ValueError("Station ownership mismatch")
        values = {
            "source": source,
            "operator_id": operator_id,
            "external_id": record["external_id"],
            "name": record["name"],
            "district": record["district"],
            "address": record["address"],
            "power_kw": Decimal(record["power_kw"]),
            "connectors": sum(len(e["connectors"]) for e in record["equipment"]),
            "synced_at": now(),
            **station_metadata.values(record),
        }
        sid = db.scalar(
            insert(Station)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["source", "external_id"],
                set_={
                    k: v
                    for k, v in values.items()
                    if k not in {"source", "external_id", "operator_id"}
                },
                where=Station.operator_id == operator_id,
            )
            .returning(Station.id)
        )
        if sid is None:
            raise ValueError("Station ownership mismatch")
        for equipment in record["equipment"]:
            eid = db.scalar(
                insert(Equipment)
                .values(
                    station_id=sid,
                    external_id=equipment["external_id"],
                    name=equipment["name"],
                    power_kw=Decimal(equipment["power_kw"]),
                )
                .on_conflict_do_update(
                    index_elements=["station_id", "external_id"],
                    set_={"name": equipment["name"], "power_kw": Decimal(equipment["power_kw"])},
                )
                .returning(Equipment.id)
            )
            equipment_count += 1
            for connector in equipment["connectors"]:
                db.execute(
                    insert(Connector)
                    .values(equipment_id=eid, **connector)
                    .on_conflict_do_update(
                        index_elements=["equipment_id", "external_id"],
                        set_={"status": connector["status"]},
                    )
                )
                connector_count += 1
        if "subsidy_device_scopes" in record:
            subsidy_device_scopes.ingest(
                db,
                source,
                operator_id,
                db.get(Station, sid),
                record["subsidy_device_scopes"],
                is_demo=(source == "demo"),
            )
        if "subsidy_device_days" in record:
            result = subsidy_device_days.ingest(
                db,
                source,
                operator_id,
                db.get(Station, sid),
                record["subsidy_device_days"],
                is_demo=(source == "demo"),
            )
            device_day_count += result["received"]
        if "orders" in record:
            result = orders.ingest(
                db,
                source,
                operator_id,
                db.get(Station, sid),
                record["orders"],
                is_demo=(source == "demo"),
            )
            order_count += result["received"]
        if "runtime_intervals" in record:
            result = runtime.ingest(
                db,
                source,
                operator_id,
                db.get(Station, sid),
                record["runtime_intervals"],
                is_demo=(source == "demo"),
            )
            runtime_count += result["received"]
        if "faults" in record:
            result = faults.ingest(
                db,
                source,
                operator_id,
                db.get(Station, sid),
                record["faults"],
                is_demo=(source == "demo"),
            )
            fault_count += result["received"]
        if "hourly_energy" in record:
            result = hourly_energy.ingest(
                db,
                source,
                operator_id,
                db.get(Station, sid),
                record["hourly_energy"],
                is_demo=(source == "demo"),
            )
            hourly_count += result["received"]
    return {
        "stations": len(records),
        "equipment": equipment_count,
        "connectors": connector_count,
        "orders": order_count,
        "hourly_energy": hourly_count,
        "faults": fault_count,
        "runtime_intervals": runtime_count,
        "subsidy_device_days": device_day_count,
    }


def execute(claimed, adapter: Adapter | None = None):
    task_id, token, operator_id, source = claimed
    try:
        if source != "demo":
            raise ValueError("Source adapter not configured")
        with lease_heartbeat(task_id, token) as lease_lost:
            records = (adapter or DemoAdapter()).fetch(operator_id)
        if lease_lost.is_set():
            return False
        with SessionLocal.begin() as db:
            task = db.scalar(select(SyncTask).where(SyncTask.id == task_id).with_for_update())
            if task.status != "running" or task.lease_owner != token or task.lease_until <= now():
                return False
            task.result = ingest(db, source, operator_id, records)
            task.status, task.completed_at, task.error = "succeeded", now(), None
            task.lease_owner, task.lease_until = None, None
        return True
    except Exception:  # noqa: BLE001 - worker boundary persists a sanitized failure for recovery
        # Do not persist upstream payloads or exception strings that may contain credentials.
        with SessionLocal.begin() as db:
            task = db.scalar(select(SyncTask).where(SyncTask.id == task_id).with_for_update())
            if task and task.lease_owner == token:
                task.status = "retry" if task.attempts < task.max_attempts else "failed"
                task.error = "同步失败，请检查适配器及输入数据；可在任务记录查看尝试次数"
                task.available_at = now() + timedelta(seconds=30 * task.attempts)
                task.lease_owner, task.lease_until = None, None
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    while True:
        task = claim()
        if task:
            execute(task)
        if args.once:
            break
        if not task:
            time.sleep(2)


if __name__ == "__main__":
    main()
