from typing import Literal

from pydantic import BaseModel, Field, computed_field

from app.decimal_contracts import DecimalText
from app.subsidy_device_days import SourceDeviceDay, ratio
from app.subsidy_rules import DeviceExecutableRules


class SubsidyRatingReference(BaseModel):
    application_id: int
    action_id: int
    published_version: int
    report_id: str
    published_at: str
    action_at: str
    valid_until: str
    as_of: str
    stars: int = Field(ge=0, le=5, strict=True)
    report_sha256: str
    is_demo: Literal[True]


class FrozenDeviceScope(BaseModel):
    id: int
    version: int
    external_id: str
    day: str
    equipment_external_ids: list[str]
    source_updated_at: str
    is_demo: Literal[True]


class DeviceClaimCalculation(BaseModel):
    execution_version: Literal[2]
    policy_rules: DeviceExecutableRules
    rating: SubsidyRatingReference
    star_coefficient: DecimalText
    pre_coefficient_amount: DecimalText
    calculated_amount: DecimalText
    observed_kwh: DecimalText
    row_count: int
    eligible_rows: int
    excluded_rows: int
    scopes: list[FrozenDeviceScope]


class DeviceLineCalculation(BaseModel):
    fact_id: int
    fact_version: int
    equipment_id: int
    scope_id: int
    scope_version: int
    source: SourceDeviceDay
    is_demo: Literal[True]
    eligible: bool
    exclusion_reason: Literal["fault_threshold", "offline_threshold", "both_thresholds"] | None
    rate: DecimalText
    amount: DecimalText


class DeviceLineResponse(BaseModel):
    claim_id: int
    fact_id: int
    snapshot: DeviceLineCalculation

    @computed_field
    @property
    def fault_pct(self) -> DecimalText | None:
        return ratio(self.snapshot.source, "fault")[0]

    @computed_field
    @property
    def offline_pct(self) -> DecimalText | None:
        return ratio(self.snapshot.source, "offline")[0]


class DeviceLinePage(BaseModel):
    items: list[DeviceLineResponse]
    page: int
    page_size: int
    total: int
