"""Core business wire contracts. Financial and score values stay finite decimal text."""

from typing import Literal

from pydantic import BaseModel, Field, JsonValue

from app.contracts import RatingCalculationSummary
from app.decimal_contracts import DecimalText
from app.material_contracts import ApplicationMaterialItem
from app.subsidy_execution_contracts import DeviceClaimCalculation
from app.subsidy_rules import DeviceExecutableRules, DeviceSubsidyRules


class SubsidyPolicyResponse(BaseModel):
    device_rules: DeviceSubsidyRules | DeviceExecutableRules | None
    calculation_basis: Literal[
        "legacy_station_energy", "device_rules_pending", "demo_device_day_v2"
    ]
    claim_supported: bool
    id: int
    code: str
    version: int
    name: str
    rate: DecimalText
    effective_from: str
    effective_to: str
    enabled: bool
    is_demo: bool


class SubsidyPolicyList(BaseModel):
    items: list[SubsidyPolicyResponse]


class SubsidyEnergyItem(BaseModel):
    id: int
    day: str
    kwh: DecimalText
    source: str
    eligible: bool


class SubsidyEnergyList(BaseModel):
    items: list[SubsidyEnergyItem]


class SubsidyClaimResponse(BaseModel):
    application_note: str | None
    id: int
    station_id: int
    operator_id: int
    policy_id: int
    start_date: str
    end_date: str
    status: str
    version: int
    total_kwh: DecimalText
    base_amount: DecimalText
    adjusted_amount: DecimalText
    is_demo: bool
    created_at: str


class SubsidyBatchResult(BaseModel):
    items: list[SubsidyClaimResponse]
    total: int = Field(ge=1, le=100)


class SubsidyCalculationRow(BaseModel):
    energy_id: int
    day: str
    kwh: DecimalText
    rate: DecimalText
    amount: DecimalText


class SubsidyMaterialItem(ApplicationMaterialItem):
    submitted_version: int | None


class SubsidyMaterialList(BaseModel):
    items: list[SubsidyMaterialItem]
    version: int
    total_bytes: int = Field(ge=0)
    limit_bytes: int = Field(gt=0)


class SubsidyMaterialMutation(BaseModel):
    material: SubsidyMaterialItem
    version: int


class SubsidyMaterialDeleted(BaseModel):
    ok: bool
    version: int


class SubsidySubmissionPacket(BaseModel):
    version: int
    note: str
    materials: list[SubsidyMaterialItem]


class SubsidySnapshot(BaseModel):
    device_calculation: DeviceClaimCalculation | None = None
    application_year: int | None = None
    submission: SubsidySubmissionPacket | None = None
    # Missing legacy facts remain unknown; never manufacture empty/zero calculations.
    policy_code: str | None = None
    policy_version: int | None = None
    policy_rate: DecimalText | None = None
    rounding: str | None = None
    details: list[SubsidyCalculationRow] | None = None


class SubsidyHistoryItem(BaseModel):
    action: str
    comment: str
    delta: DecimalText
    created_at: str


class SubsidyClaimDetail(SubsidyClaimResponse):
    snapshot: SubsidySnapshot | None
    history: list[SubsidyHistoryItem]


class RatingBandResponse(BaseModel):
    lower: DecimalText
    upper: DecimalText | None
    score: DecimalText
    label: str


class RatingFieldResponse(BaseModel):
    code: Literal["expert_score", "station_power_kw", "equipment_count", "connector_count"]
    name: str
    source: Literal["manual", "runtime"]
    unit: str
    description: str


class RatingFieldList(BaseModel):
    items: list[RatingFieldResponse]


class RatingEvaluationContext(BaseModel):
    evaluated_on: str
    captured_at: str
    station_id: int
    basis: str
    completeness_verified: bool
    fields: dict[str, DecimalText | None]


class RatingIndicatorResponse(BaseModel):
    enabled: bool | None = None
    effective_from: str | None = None
    description: str | None = None
    field_name: (
        Literal["expert_score", "station_power_kw", "equipment_count", "connector_count"] | None
    ) = None
    bands: list[RatingBandResponse] | None = None
    code: str
    name: str
    weight: DecimalText
    max_score: DecimalText
    source: str
    category: str | None = None


class RatingCategoryResponse(BaseModel):
    code: str
    name: str
    weight: DecimalText


class RatingThresholdResponse(BaseModel):
    minimum: DecimalText
    stars: int


class RatingRulesResponse(BaseModel):
    indicator_status_version: int | None = None
    configuration_version: int | None = None
    evaluation_context: RatingEvaluationContext | None = None
    categories: list[RatingCategoryResponse] | None = None
    indicators: list[RatingIndicatorResponse] | None = None
    thresholds: list[RatingThresholdResponse] | None = None


class RatingStandardResponse(BaseModel):
    revision: int
    archived: bool
    effective_on: str | None
    activation_state: str
    id: int
    code: str
    version: int
    name: str
    rules: RatingRulesResponse
    is_demo: bool
    enabled: bool


class RatingStandardList(BaseModel):
    items: list[RatingStandardResponse]


class RatingReferenceResponse(BaseModel):
    id: int | None = None
    version: int | None = None
    status: str | None = None
    stars: int | None = None
    valid_until: str | None = None
    result_state: str | None = None
    effective_stars: int | None = None
    show_association_mark: bool | None = None


class RatingCategoryCalculation(RatingCategoryResponse):
    indicator_weight_sum: DecimalText | None = None
    score: DecimalText | None
    missing: list[str] = Field(default_factory=list)


class RatingIndicatorCalculation(BaseModel):
    enabled: bool | None = None
    normalization_weight: DecimalText | None = None
    effective_weight_percent: DecimalText | None = None
    exclusion_reason: str | None = None
    effective_from: str | None = None
    field_name: str | None = None
    raw_value: DecimalText | None = None
    band_label: str | None = None
    missing_reason: str | None = None
    code: str
    name: str
    category: str | None = None
    score: DecimalText | None
    source: str | None = None
    max_score: DecimalText | None = None
    weight: DecimalText | None = None
    weighted_score: DecimalText | None = None


class RatingCalculationResponse(RatingCalculationSummary):
    excluded: list[str] | None = None
    total: DecimalText | None = None
    categories: list[RatingCategoryCalculation] | None = None
    details: list[RatingIndicatorCalculation] | None = None


class RatingApplicationResponse(BaseModel):
    id: int
    station_id: int
    operator_id: int
    standard_id: int
    status: str
    kind: str
    parent_id: int | None
    before_result: RatingReferenceResponse | None
    valid_until: str | None
    published_at: str | None
    has_report: bool
    result_state: str
    effective_stars: int | None
    show_association_mark: bool
    version: int
    calculation: RatingCalculationResponse
    is_demo: bool
    evidence_note: str
    created_at: str


class RatingHistoryItem(BaseModel):
    action: str
    comment: str
    # Earlier workflow versions have different frozen payload shapes; retain as JSON.
    payload: dict[str, JsonValue]
    created_at: str


class RatingSubmissionReceipt(BaseModel):
    action_id: int
    actor_id: int
    current_account_username: str | None
    submitted_at: str


class RatingApplicationDetail(RatingApplicationResponse):
    station_name: str
    operator_name: str
    last_submission: RatingSubmissionReceipt | None
    rule_snapshot: RatingRulesResponse
    initial_scores: dict[str, DecimalText | None]
    current_scores: dict[str, DecimalText | None]
    history: list[RatingHistoryItem]


class RatingMaterialItem(BaseModel):
    id: int
    filename: str
    content_type: str
    size: int = Field(ge=0)
    sha256: str
    created_at: str
    category: str
    category_name: str
    submitted_version: int | None


class RatingMaterialList(BaseModel):
    items: list[RatingMaterialItem]
    categories: dict[str, str]
    total_bytes: int = Field(ge=0)
    limit_bytes: int = Field(gt=0)


class SubsidyAnnualStation(BaseModel):
    id: int
    name: str
    district: str
    status: str
    equipment_count: int
    equipment_power_kw: DecimalText | None
    selectable: bool
    unavailable_reason: str | None


class SubsidyAnnualStations(BaseModel):
    items: list[SubsidyAnnualStation]
    total: int
    page: int
    page_size: int
    default_year: int
    current_year: int


class SubsidyAnnualReceipt(BaseModel):
    batch_id: int
    application_year: int
    mode: str
    # Immutable creation receipt, not the current state of each claim.
    items: list[SubsidyClaimResponse]


class SubsidyAnnualResult(BaseModel):
    replayed: bool
    receipt: SubsidyAnnualReceipt


class RatingWeightItem(BaseModel):
    code: str
    name: str
    category: str
    enabled: bool
    configured_weight: DecimalText
    normalization_weight: DecimalText
    effective_weight_percent: DecimalText


class RatingWeightCategory(BaseModel):
    code: str
    name: str
    weight: DecimalText
    indicator_weight_sum: DecimalText


class RatingWeightPlan(BaseModel):
    items: list[RatingWeightItem]
    categories: list[RatingWeightCategory]
    excluded: list[str]
    basis: str
