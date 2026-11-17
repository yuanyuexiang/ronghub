"""Source-reported profile values; omission preserves, explicit null clears.

The unique code is a supplied descriptive field, not an inferred primary key or
an asserted globally unique identifier. Interface descriptions remain source text
until the customer supplies a formal dictionary. Runtime intervals stay separate.
"""

from datetime import date, datetime
from typing import Literal

EquipmentType = Literal["dc", "ac", "ac_dc", "wireless", "ordered", "v2g", "other"]
TYPES = {
    "dc": "直流设备",
    "ac": "交流设备",
    "ac_dc": "交直流一体设备",
    "wireless": "无线设备",
    "ordered": "有序设备",
    "v2g": "V2G设备",
    "other": "其他设备",
}
EQUIPMENT_FIELDS = ("equipment_type", "model_name", "source_unique_code", "manufactured_on")
CONNECTOR_FIELDS = ("name", "interface_type")
STATES = {
    "available": "空闲",
    "charging": "充电中",
    "occupied": "占用",
    "reserved": "已预约",
    "fault": "故障",
    "faulted": "故障（来源faulted）",
    "offline": "离线",
    "unknown": "状态未知",
    "other": "其他",
}


def string(value, limit):
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) > limit
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise ValueError("Invalid source device profile text")
    return value.strip() or None


def equipment_values(record):
    result = {}
    if "equipment_type" in record:
        value = record["equipment_type"]
        if value is not None and (not isinstance(value, str) or value not in TYPES):
            raise ValueError("Unknown source equipment type")
        result["equipment_type"] = value
    for key in ["model_name", "source_unique_code"]:
        if key in record:
            result[key] = string(record[key], 100)
    if "manufactured_on" in record:
        value = record["manufactured_on"]
        if isinstance(value, str):
            try:
                parsed = date.fromisoformat(value)
            except ValueError as error:
                raise ValueError("Invalid source manufacturing date") from error
            if parsed.isoformat() != value:
                raise ValueError("Source manufacturing date requires YYYY-MM-DD")
            value = parsed
        if value is not None and (
            isinstance(value, datetime)
            or not isinstance(value, date)
            or not 1900 <= value.year <= 9998
        ):
            raise ValueError("Invalid source manufacturing date")
        result["manufactured_on"] = value
    return result


def connector_values(record):
    external_id = string(record["external_id"], 100)
    status = string(record["status"], 40)
    if not external_id or not status:
        raise ValueError("Connector identity and snapshot status must not be blank")
    result = {"external_id": record["external_id"], "status": record["status"]}
    for key, limit in [("name", 150), ("interface_type", 100)]:
        if key in record:
            result[key] = string(record[key], limit)
    return result


def equipment_profile(row):
    return {
        "id": row.id,
        "external_id": row.external_id,
        "name": row.name,
        "power_kw": str(row.power_kw),
        "equipment_type": row.equipment_type,
        "model_name": row.model_name,
        "source_unique_code": row.source_unique_code,
        "manufactured_on": row.manufactured_on.isoformat() if row.manufactured_on else None,
    }


def connector_profile(row):
    return {
        "id": row.id,
        "external_id": row.external_id,
        "status": row.status,
        "name": row.name,
        "interface_type": row.interface_type,
    }
