"""Typed independent demo observations and frozen event evidence."""

from datetime import UTC, date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from app.alert_configuration import Configuration, Number
from app.alert_period_contracts import SourcePeriodProof
from app.decimal_contracts import DecimalText

Id = int
Level = Literal["general", "severe", "critical"]
Outcome = Literal["opened", "merged", "recovered", "normal", "unknown_coverage", "ignored_stale"]


class ObservationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID
    station_id: int = Field(strict=True, ge=1, le=2147483647)
    rule_id: int = Field(strict=True, ge=1, le=2147483647)
    equipment_id: int | None = Field(default=None, strict=True, ge=1, le=2147483647)
    observed_at: datetime
    value: Number | None
    rated_current_a: Number | None = None
    period_start: date | None = None
    period_end: date | None = None
    coverage_complete: StrictBool

    @field_validator("observed_at")
    @classmethod
    def aware(cls, value):
        if value.utcoffset() is None:
            raise ValueError("观测时间须包含时区")
        try:
            value.astimezone(UTC)
        except (OverflowError, ValueError) as error:
            raise ValueError("观测时间超出支持范围") from error
        return value


class EquipmentEvidence(BaseModel):
    id: int
    external_id: str
    name: str


class ObservationEvidence(BaseModel):
    evidence_version: Literal[1]
    source_basis: Literal["manual_demo_v1", "adapter_period_demo_v1"]
    source_period: SourcePeriodProof | None = None
    operator_id: int
    station_id: int
    station_source: str
    station_external_id: str
    equipment: EquipmentEvidence | None
    rule_id: int
    rule_code: str
    rule_version: int
    configuration: Configuration
    observed_at: str
    value: DecimalText | None
    rated_current_a: DecimalText | None
    period_start: str | None
    period_end: str | None
    coverage_complete: bool
    comparison_value: DecimalText | None
    is_demo: Literal[True]


class EventEvidence(BaseModel):
    evidence_version: Literal[1]
    first_observation_id: int
    latest_observation_id: int
    first: ObservationEvidence
    latest: ObservationEvidence
    current_level: Level | None
    highest_level: Level


class ObservationResult(BaseModel):
    observation_id: int
    request_key: str
    outcome: Outcome
    event_id: int | None
    level: Level | None
    comparison_value: DecimalText | None
    is_demo: Literal[True]


class ObservationRow(BaseModel):
    id: int
    request_key: str
    station_id: int
    station_name: str
    operator_id: int
    operator_name: str
    created_by: int | None
    created_at: str
    evidence: ObservationEvidence
    result: ObservationResult


class ObservationPage(BaseModel):
    items: list[ObservationRow]
    total: int
    page: int
    page_size: int


class StationOption(BaseModel):
    id: int
    name: str


class ObservationStationPage(BaseModel):
    items: list[StationOption]
    total: int
    page: int
    page_size: int


class EquipmentOption(StationOption):
    external_id: str
    power_kw: DecimalText


class ObservationEquipmentPage(BaseModel):
    items: list[EquipmentOption]
    total: int
    page: int
    page_size: int
