"""Normalized source facilities, independent of platform approval and connection time.

Adapters translate documented source codes; absence preserves stored values while
explicit null clears a field. No source capacity unit or telephone format is guessed.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel

ENUMS = {
    "supply_type": {"direct", "transferred"},
    "electricity_tariff": {"commercial", "general_industrial", "large_industrial", "other"},
    "parking_fee_type": {"free", "paid", "limited_free", "charging_discount", "onsite"},
}
TEXT = {
    "parking_fee_description": 500,
    "business_hours": 255,
    "filing_code": 100,
    "station_contact_name": 100,
    "station_contact_phone": 80,
}
FIELDS = (
    *ENUMS,
    "supply_capacity",
    "supply_capacity_unit",
    "independent_connection",
    "parking_spaces",
    "commissioned_on",
    *TEXT,
)


class Facilities(BaseModel):
    supply_type: Literal["direct", "transferred"] | None
    electricity_tariff: (
        Literal["commercial", "general_industrial", "large_industrial", "other"] | None
    )
    supply_capacity: str | None
    supply_capacity_unit: Literal["unknown", "kW", "kVA"] | None
    independent_connection: bool | None
    parking_spaces: int | None
    commissioned_on: str | None
    parking_fee_type: Literal["free", "paid", "limited_free", "charging_discount", "onsite"] | None
    parking_fee_description: str | None
    business_hours: str | None
    filing_code: str | None


class Contact(BaseModel):
    name: str | None
    phone: str | None


def values(record):
    result = {}
    for key, options in ENUMS.items():
        if key in record:
            value = record[key]
            if value is not None and (not isinstance(value, str) or value not in options):
                raise ValueError("Unknown station facility classification")
            result[key] = value
    for key, limit in TEXT.items():
        permitted_controls = "\n\t" if key in {"parking_fee_description", "business_hours"} else ""
        if key not in record:
            continue
        value = record[key]
        if value is not None:
            if (
                not isinstance(value, str)
                or len(value) > limit
                or any(
                    (ord(c) < 32 and c not in permitted_controls) or ord(c) == 127 for c in value
                )
            ):
                raise ValueError("Invalid station facility text")
            value = value.strip() or None
        result[key] = value
    if "independent_connection" in record:
        value = record["independent_connection"]
        if value is not None and type(value) is not bool:
            raise ValueError("Independent connection requires a boolean or null")
        result["independent_connection"] = value
    if "parking_spaces" in record:
        value = record["parking_spaces"]
        if value is not None and (type(value) is not int or not 0 <= value <= 2147483647):
            raise ValueError("Invalid parking space count")
        result["parking_spaces"] = value
    if "commissioned_on" in record:
        value = record["commissioned_on"]
        if value is not None:
            if isinstance(value, str):
                try:
                    parsed = date.fromisoformat(value)
                except ValueError as error:
                    raise ValueError("Invalid source commissioning date") from error
                if parsed.isoformat() != value:
                    raise ValueError("Source commissioning date must be YYYY-MM-DD")
                value = parsed
            if (
                isinstance(value, datetime)
                or not isinstance(value, date)
                or not 1900 <= value.year <= 9998
            ):
                raise ValueError("Invalid source commissioning date")
        result["commissioned_on"] = value
    # A pair must be supplied together, including explicit clearing. This avoids
    # combining a new value with a stale unit from a previous payload.
    if "supply_capacity" in record or "supply_capacity_unit" in record:
        if "supply_capacity" not in record or "supply_capacity_unit" not in record:
            raise ValueError("Source capacity requires value and unit together")
        value, unit = record["supply_capacity"], record["supply_capacity_unit"]
        if value is None and unit is None:
            result.update(supply_capacity=None, supply_capacity_unit=None)
        else:
            if (
                not isinstance(unit, str)
                or unit not in {"unknown", "kW", "kVA"}
                or type(value) not in {str, int, Decimal}
            ):
                raise ValueError("Invalid source capacity value or unit")
            try:
                number = Decimal(value)
            except (InvalidOperation, ValueError) as error:
                raise ValueError("Invalid source capacity") from error
            if (
                not number.is_finite()
                or not 0 <= number <= Decimal("99999999999.999")
                or number != number.quantize(Decimal("0.001"))
            ):
                raise ValueError("Source capacity exceeds supported precision")
            result.update(supply_capacity=number, supply_capacity_unit=unit)
    return result


def public(station):
    fields = Facilities.model_fields
    result = {key: getattr(station, key) for key in fields}
    for key in ["supply_capacity", "commissioned_on"]:
        if result[key] is not None:
            result[key] = str(result[key])
    return result
