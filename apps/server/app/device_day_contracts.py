from typing import Literal

from pydantic import BaseModel, Field

from app.business_contracts import DecimalText

Reason = Literal["missing", "basis_unconfirmed", "partial", "unknown_states"]


class SubsidyDeviceDayResponse(BaseModel):
    id: int
    station_id: int
    equipment_id: int
    operator_id: int
    station_name: str
    operator_name: str
    district: str
    current_equipment_name: str
    source: str
    external_id: str
    equipment_external_id: str
    equipment_name: str | None
    equipment_type: Literal["dc", "ac", "swapping", "unknown"]
    power_kw: DecimalText | None
    production_date: str | None
    day: str
    business_kind: Literal["charging", "swapping", "unknown"]
    energy_kwh: DecimalText | None
    energy_complete: bool
    metric_basis: Literal["calendar_day_seconds_v1", "unconfirmed"]
    fault_observed_seconds: DecimalText | None
    fault_unknown_seconds: DecimalText | None
    fault_seconds: DecimalText | None
    offline_observed_seconds: DecimalText | None
    offline_unknown_seconds: DecimalText | None
    offline_seconds: DecimalText | None
    fault_percent: DecimalText | None
    offline_percent: DecimalText | None
    fault_percent_reason: Reason | None
    offline_percent_reason: Reason | None
    observation_complete: bool
    mapping_basis: Literal["source_station_equipment_key"]
    settlement_verified: Literal[False]
    source_updated_at: str
    synced_at: str
    version: int
    is_demo: bool


class SubsidyDeviceDayPage(BaseModel):
    items: list[SubsidyDeviceDayResponse]
    total: int = Field(ge=0)
    page: int
    page_size: int
    notice: str
