"""Explicit public response shapes; decimal values remain strings on the wire."""

from typing import Literal

from pydantic import BaseModel, Field, JsonValue

from app.alert_configuration import Configuration
from app.configured_alert_contracts import EventEvidence
from app.device_metadata import EquipmentType
from app.inspection_documents import ExecutionContact
from app.richtext import RichDocument
from app.station_facilities import Contact as StationContact
from app.station_facilities import Facilities as StationFacilities


class ValidationIssue(BaseModel):
    type: str
    loc: list[str | int]
    msg: str


class ErrorResponse(BaseModel):
    detail: str | list[ValidationIssue]


class LoginResponse(BaseModel):
    username: str
    role: Literal["regulator", "association", "operator"]
    csrf_token: str


class CurrentUser(LoginResponse):
    operator_id: int | None
    role_profile_id: int | None
    allowed_districts: list[str] | None
    demo_mode: bool
    must_change_password: bool
    permissions: list[str]


class OkResponse(BaseModel):
    ok: bool


class StationItem(BaseModel):
    id: int
    name: str
    district: str
    address: str
    status: str
    operator_id: int
    connectors: int
    power_kw: str
    version: int
    synced_at: str


class StationPage(BaseModel):
    items: list[StationItem]
    total: int
    page: int
    page_size: int


class StationDetail(BaseModel):
    source_first_reported_at: str | None
    facilities: StationFacilities
    contact_accessible: bool
    contact: StationContact | None
    source: str
    external_id: str
    id: int
    name: str
    status: str
    version: int
    address: str
    power_kw: str
    district: str
    usage_type: str
    construction_site: str
    site_state: str
    source_sync_state: str
    source_first_connected_at: str | None
    operator_id: int
    station_type: str
    longitude: str | None
    latitude: str | None
    coordinate_system: str
    location_is_demo: bool
    synced_at: str


class ConnectorItem(BaseModel):
    name: str | None
    interface_type: str | None
    id: int
    external_id: str
    status: str


class EquipmentItem(BaseModel):
    external_id: str
    equipment_type: EquipmentType | None
    model_name: str | None
    source_unique_code: str | None
    manufactured_on: str | None
    id: int
    name: str
    power_kw: str
    connectors: list[ConnectorItem]


class EquipmentList(BaseModel):
    items: list[EquipmentItem]


class OperatorItem(BaseModel):
    id: int
    name: str


class OperatorList(BaseModel):
    items: list[OperatorItem]


class AuditItem(BaseModel):
    id: int
    actor: str
    action: str
    object_id: int | None
    created_at: str
    result: Literal["committed"]
    detail: dict[str, JsonValue]


class AuditPage(BaseModel):
    items: list[AuditItem]
    total: int
    page: int
    page_size: int
    notice: str


class StationApplicationRecord(BaseModel):
    id: int
    station_id: int
    station_address: str
    operator_id: int
    station_name: str
    operator_name: str
    kind: str
    status: str
    version: int
    payload: dict[str, JsonValue]
    rule_version: str
    created_at: str
    last_submitted_at: str | None
    active_material_count: int


class StationApplicationHistory(BaseModel):
    action: str
    comment: str
    created_at: str


class StationApplicationDetail(StationApplicationRecord):
    history: list[StationApplicationHistory]


class StationApplicationPage(BaseModel):
    items: list[StationApplicationRecord]
    total: int
    page: int
    page_size: int


class SubsidyQueueItem(BaseModel):
    application_year: int | None = None
    last_submitted_at: str | None
    id: int
    station_id: int
    operator_id: int
    station_name: str
    operator_name: str
    policy_id: int
    start_date: str
    end_date: str
    status: str
    version: int
    total_kwh: str
    base_amount: str
    adjusted_amount: str
    is_demo: bool
    created_at: str


class SubsidyQueuePage(BaseModel):
    items: list[SubsidyQueueItem]
    total: int
    page: int
    page_size: int


class RatingCalculationSummary(BaseModel):
    total: str | None = None
    stars: int | None = None
    missing: list[str] = Field(default_factory=list)
    basis: str = "历史计算口径未记录"
    categories: list[dict[str, JsonValue]] | None = None
    details: list[dict[str, JsonValue]] | None = None
    formula: str | None = None


class StationRatingResult(BaseModel):
    result_state: str
    effective_stars: int | None
    valid_until: str | None


class RatingQueueItem(BaseModel):
    last_submitted_at: str | None
    station_code: str
    current_station_result: StationRatingResult | None
    id: int
    station_id: int
    operator_id: int
    station_name: str
    operator_name: str
    standard_id: int
    status: str
    kind: str
    parent_id: int | None
    before_result: dict[str, JsonValue] | None
    valid_until: str | None
    published_at: str | None
    has_report: bool
    result_state: str
    effective_stars: int | None
    show_association_mark: bool
    version: int
    calculation: RatingCalculationSummary
    is_demo: bool
    evidence_note: str
    created_at: str


class RatingQueuePage(BaseModel):
    items: list[RatingQueueItem]
    total: int
    page: int
    page_size: int


class InspectionTaskResponse(BaseModel):
    id: int
    station_id: int
    plan_id: int | None
    operator_id: int
    title: str
    description: str
    rich_document: RichDocument | None
    severity: str
    due_at: str | None
    status: str
    version: int
    overdue: bool
    is_demo: bool
    created_at: str


class InspectionHistoryItem(BaseModel):
    id: int
    action: str
    comment: str
    rich_document: RichDocument | None
    contact: ExecutionContact | None
    performed_at: str | None
    created_at: str


class InspectionDetailResponse(InspectionTaskResponse):
    history: list[InspectionHistoryItem]


class InspectionQueueItem(InspectionTaskResponse):
    station_name: str
    operator_name: str
    execution_state: Literal["reported", "not_reported", "unknown"]
    last_reply_id: int | None
    last_reply_submitted_at: str | None
    last_reported_performed_at: str | None


class InspectionQueuePage(BaseModel):
    items: list[InspectionQueueItem]
    total: int
    page: int
    page_size: int
    query_at: str


class AlertRuleSnapshot(BaseModel):
    configuration: Configuration | None = None
    notifications_enabled: bool | None = None
    code: str
    version: int
    metric: str
    unit: str
    trigger: str
    recover: str


class AlertEventResponse(BaseModel):
    configuration_evidence: EventEvidence | None = None
    id: int
    station_id: int
    target: str
    status: str
    opened_at: str
    last_observed_at: str
    recovered_at: str | None
    occurrences: int
    last_value: str
    rule_snapshot: AlertRuleSnapshot
    acknowledged_at: str | None
    acknowledge_note: str
    version: int
    is_demo: bool


class AlertQueueItem(AlertEventResponse):
    operator_id: int
    rule_id: int
    station_name: str
    operator_name: str


class AlertNoticeResponse(BaseModel):
    phase: str
    message: str
    created_at: str
    channel: str


class AlertDetail(AlertEventResponse):
    notices: list[AlertNoticeResponse]


class AlertQueuePage(BaseModel):
    items: list[AlertQueueItem]
    total: int
    page: int
    page_size: int


class ChargingOrderItem(BaseModel):
    id: int
    operator_id: int
    station_id: int
    station_name: str
    operator_name: str
    district: str
    source: str
    external_id: str
    equipment_external_id: str | None
    connector_external_id: str | None
    status: str
    started_at: str
    ended_at: str | None
    duration_seconds: int | None
    kwh: str | None
    amount: str | None
    currency: str | None
    source_updated_at: str
    synced_at: str
    version: int
    is_demo: bool


class ChargingOrderPage(BaseModel):
    items: list[ChargingOrderItem]
    total: int
    page: int
    page_size: int
    source_completeness_verified: Literal[False] = False
    notice: str


class OrderMetricCoverage(BaseModel):
    value: str | None
    known_orders: int
    missing_orders: int


class OrderSummaryItem(BaseModel):
    period_start: str
    station_id: int
    station_name: str
    operator_id: int
    operator_name: str
    district: str
    source: str
    equipment_external_id: str | None
    is_demo: bool
    observed_orders: int
    completed_orders: int
    charging_orders: int
    interrupted_orders: int
    cancelled_orders: int
    unknown_orders: int
    kwh: OrderMetricCoverage
    duration_seconds: OrderMetricCoverage
    amount: OrderMetricCoverage
    currency: Literal["CNY"] = "CNY"


class OrderSummaryPage(BaseModel):
    items: list[OrderSummaryItem]
    total: int
    page: int
    page_size: int
    dimension: Literal["station", "equipment"]
    granularity: Literal["day", "month", "year"]
    window_start_at: str
    window_end_at: str
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    generated_at: str
    source_completeness_verified: Literal[False] = False
    notice: str


class OrderTimeSlotPoint(BaseModel):
    hour: int
    label: str
    selected_slots: int
    observed_order_starts: int
    completed_order_starts: int
    order_start_share_pct: str | None
    kwh: str | None
    known_meter_slots: int
    unknown_meter_slots: int
    unreported_meter_slots: int


class OrderTimeSlotStream(BaseModel):
    is_demo: bool
    observed_order_starts: int
    slots: list[OrderTimeSlotPoint] = Field(min_length=24, max_length=24)


class OrderTimeSlotReport(BaseModel):
    station_id: int
    station_name: str
    source: str
    meter_scope: Literal["station", "equipment"]
    equipment_external_id: str | None
    start_at: str
    end_at: str
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    streams: list[OrderTimeSlotStream] = Field(max_length=2)
    generated_at: str
    source_completeness_verified: Literal[False] = False
    notice: str


class AggregateTimeSlotStream(OrderTimeSlotStream):
    stations_with_order_observations: int
    stations_with_meter_records: int


class AggregateTimeSlotReport(BaseModel):
    source: str
    station_count: int
    q: str
    district: str
    operator_id: int | None
    start_at: str
    end_at: str
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    streams: list[AggregateTimeSlotStream] = Field(max_length=2)
    generated_at: str
    source_completeness_verified: Literal[False] = False
    notice: str


class MeterSourceOption(BaseModel):
    source: str
    station_count: int


class MeterSourcePage(BaseModel):
    items: list[MeterSourceOption]
    total: int
    page: int
    page_size: int


class FaultEventItem(BaseModel):
    id: int
    station_id: int
    operator_id: int
    station_name: str
    operator_name: str
    district: str
    source: str
    external_id: str
    equipment_external_id: str | None
    connector_external_id: str | None
    fault_code: str | None
    status: Literal["unknown", "active", "recovered"]
    severity: Literal["unknown", "info", "warning", "critical"]
    started_at: str
    recovered_at: str | None
    recovery_elapsed_seconds: str | None
    source_updated_at: str
    synced_at: str
    version: int
    is_demo: bool


class FaultEventPage(BaseModel):
    items: list[FaultEventItem]
    total: int
    page: int
    page_size: int
    source_completeness_verified: Literal[False] = False
    notice: str


class FaultSummaryStream(BaseModel):
    source: str
    is_demo: bool
    observed_events: int
    active_events: int
    recovered_events: int
    unknown_events: int
    average_recovery_elapsed_seconds: str | None


class FaultSummary(BaseModel):
    streams: list[FaultSummaryStream]
    source_completeness_verified: Literal[False] = False
    notice: str


class RuntimeStateDuration(BaseModel):
    state: Literal[
        "online",
        "offline",
        "unknown",
        "available",
        "occupied",
        "charging",
        "faulted",
        "maintenance",
    ]
    observed_seconds: str
    share_of_reported_pct: str | None


class RuntimeAxis(BaseModel):
    dimension: Literal["connectivity", "operation"]
    reported_seconds: str
    known_seconds: str
    unknown_seconds: str
    unreported_seconds: str
    states: list[RuntimeStateDuration]


class RuntimeStream(BaseModel):
    is_demo: bool
    axes: list[RuntimeAxis]


class RuntimeIntervalItem(BaseModel):
    id: int
    external_id: str
    dimension: Literal["connectivity", "operation"]
    state: Literal[
        "online",
        "offline",
        "unknown",
        "available",
        "occupied",
        "charging",
        "faulted",
        "maintenance",
    ]
    is_demo: bool
    version: int
    started_at: str
    ended_at: str
    clipped_start_at: str
    clipped_end_at: str
    observed_seconds: str
    source_updated_at: str
    synced_at: str


class RuntimeReport(BaseModel):
    station_id: int
    station_name: str
    source: str
    equipment_external_id: str
    connector_external_id: str
    start_at: str
    end_at: str
    window_seconds: str
    streams: list[RuntimeStream]
    items: list[RuntimeIntervalItem]
    total: int
    page: int
    page_size: int
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    source_completeness_verified: Literal[False] = False
    notice: str


class RuntimeObject(BaseModel):
    equipment_external_id: str
    connector_external_id: str


class RuntimeObjectPage(BaseModel):
    items: list[RuntimeObject]
    total: int
    page: int
    page_size: int


class RuntimeStationOption(BaseModel):
    id: int
    name: str
    source: str


class RuntimeStationPage(BaseModel):
    items: list[RuntimeStationOption]
    total: int
    page: int
    page_size: int


class QualityFaults(BaseModel):
    observed_events: int
    stations_with_events: int
    active_events: int
    recovered_events: int
    unknown_events: int
    average_recovery_elapsed_seconds: str | None


class QualityStream(BaseModel):
    is_demo: bool
    observed_object_count: int
    stations_with_runtime: int
    observed_object_window_seconds: str
    axes: list[RuntimeAxis]
    faults: QualityFaults


class QualityGroup(BaseModel):
    key: str
    label: str
    station_count: int
    inventory_equipment_count: int
    streams: list[QualityStream]


class QualityReport(BaseModel):
    source: str
    group_by: Literal["district", "operator", "station"]
    asset_scope: Literal["equipment", "connector"]
    q: str
    district: str
    operator_id: int | None
    start_at: str
    end_at: str
    window_seconds: str
    selected_station_count: int
    summary: QualityGroup
    items: list[QualityGroup]
    total: int
    page: int
    page_size: int
    faults_scope: Literal["all_station_events_started_in_window"] = (
        "all_station_events_started_in_window"
    )
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    source_completeness_verified: Literal[False] = False
    notice: str


class ContentAttachment(BaseModel):
    id: int
    filename: str
    content_type: str
    size: int
    sha256: str
    created_at: str
    frozen: bool = False


class ContentArticle(BaseModel):
    id: int
    kind: Literal["announcement", "news", "contact", "notice", "protocol"]
    portal_public: bool
    title: str
    body: str
    rich_document: RichDocument | None = None
    audience: Literal["all", "regulator", "association", "operator"]
    operator_id: int | None
    status: Literal["draft", "published", "withdrawn", "deleted"]
    published_at: str | None
    version: int
    is_demo: bool
    created_at: str


class ContentPage(BaseModel):
    items: list[ContentArticle]
    total: int
    page: int
    page_size: int


class ContentReply(BaseModel):
    id: int
    operator_id: int
    operator_name: str
    actor_name: str
    contact_name: str | None
    contact_phone: str | None
    body: str
    rich_document: RichDocument | None = None
    created_at: str
    images: list[ContentAttachment]


class ContentHistory(BaseModel):
    action: str
    snapshot: dict[str, JsonValue]
    created_at: str


class NoticeContact(BaseModel):
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    position: str | None = None
    primary: bool | None = None


class NoticeDeliveryOutput(BaseModel):
    id: int
    operator_id: int
    operator_name: str
    contacts: list[NoticeContact]
    publication_version: int
    scheduled_at: str
    created_at: str
    state: Literal["scheduled", "visible", "withdrawn"]
    reply_count: int
    latest_reply_at: str | None


class NoticeRecord(NoticeDeliveryOutput):
    article_id: int
    title: str
    is_demo: bool


class NoticeRecordPage(BaseModel):
    items: list[NoticeRecord]
    total: int
    page: int
    page_size: int


class OperatorNotice(BaseModel):
    article_id: int
    title: str
    published_at: str
    reply_count: int
    latest_reply_at: str | None


class OperatorNoticePage(BaseModel):
    items: list[OperatorNotice]
    total: int
    page: int
    page_size: int


class ContentOperatorOption(BaseModel):
    id: int
    name: str


class ContentOperatorPage(BaseModel):
    items: list[ContentOperatorOption]
    total: int
    page: int
    page_size: int


class ContentDetail(ContentArticle):
    recipient_ids: list[int] | None = None
    deliveries: list[NoticeDeliveryOutput] = []
    replies: list[ContentReply]
    attachments: list[ContentAttachment]
    history: list[ContentHistory] | None = None


class ContentAssetMutation(BaseModel):
    article: ContentArticle
    attachments: list[ContentAttachment]


class ContentReplyCreated(BaseModel):
    id: int


class ContentBatchResult(BaseModel):
    items: list[ContentArticle]
    total: int


class LoginCaptcha(BaseModel):
    captcha_id: str
    image: str
    expires_at: str
    expires_in_seconds: int


class PhoneState(BaseModel):
    verification_mode: Literal["demo"] | None
    verified: bool
    phone_masked: str | None
    verified_at: str | None
    version: int | None
    sms_mode: Literal["disabled", "demo"]


class SmsIssued(BaseModel):
    challenge_id: str
    expires_at: str
    expires_in_seconds: int
    retry_after_seconds: int
    delivery_mode: Literal["demo"]
    message: str


class PhoneBound(BaseModel):
    ok: bool
    reauthenticate: bool
    phone_masked: str
    verified_at: str


class SmsAuthOptions(BaseModel):
    sms_mode: Literal["disabled", "demo"]
    demo_mode: bool


class PasswordRecovered(BaseModel):
    ok: bool
    reauthenticate: bool


class PasswordChanged(PasswordRecovered):
    pass


class PasswordResetResult(OkResponse):
    user_id: int
    version: int = Field(ge=1)
    must_change_password: bool


class StaffEnrollmentOptions(BaseModel):
    enabled: bool
    demo_mode: bool


class StaffApplicantStatus(BaseModel):
    id: int
    requested_role: Literal["regulator", "association"]
    applicant_name: str
    phone_masked: str
    status: Literal["pending", "approved", "rejected"]
    version: int
    created_at: str
    reviewed_at: str | None
    review_comment: str | None
    username: str | None
    notification_status: Literal["not_sent"]
    is_demo: bool


class StaffGrantSnapshot(BaseModel):
    role_profile_id: int
    profile_name: str
    profile_version: int
    base_role: Literal["regulator", "association"]
    permissions: list[str]
    allowed_districts: list[str] | None


class StaffApplicationRecord(StaffApplicantStatus):
    contact_phone: str
    user_id: int | None
    grant_snapshot: StaffGrantSnapshot | None


class StaffActionRecord(BaseModel):
    actor_name: str
    action: str
    snapshot: dict[str, JsonValue]
    created_at: str


class StaffApplicationDetail(StaffApplicationRecord):
    history: list[StaffActionRecord]


class StaffApplicationPage(BaseModel):
    items: list[StaffApplicationRecord]
    total: int
    page: int
    page_size: int


class StaffRoleOption(BaseModel):
    id: int
    name: str
    base_role: Literal["regulator", "association"]
    permissions: list[str]
    version: int


class StaffRoleOptions(BaseModel):
    items: list[StaffRoleOption]
    total: int
    page: int
    page_size: int


class DashboardSummary(BaseModel):
    stations: int
    connectors: int
    power_kw: str
    pending: int | None
    demo_mode: bool
    rating_pending: int | None = None
    rating_reviewed: int | None = None
    rating_published: int | None = None
    applications_pending: int | None = None
    applications_draft: int | None = None
    applications_rejected: int | None = None
    subsidies_pending: int | None = None
    subsidy_total: str | None = None
    energy_total: str | None = None
    staff_applications_pending: int | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"]
    demo_mode: bool
