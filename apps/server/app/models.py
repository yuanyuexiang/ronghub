from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def now():
    return datetime.now(UTC)


class Operator(Base):
    __tablename__ = "operators"
    __table_args__ = (UniqueConstraint("credit_code", name="uq_operators_credit_code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    credit_code: Mapped[str | None] = mapped_column(String(18))
    registration_address: Mapped[str] = mapped_column(String(255), default="", server_default="")
    legal_representative: Mapped[str] = mapped_column(String(100), default="", server_default="")
    service_phone: Mapped[str] = mapped_column(String(80), default="", server_default="")
    contacts: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    enabled: Mapped[bool] = mapped_column(default=True, server_default="true")
    version: Mapped[int] = mapped_column(default=1, server_default="1")


class RoleProfile(Base):
    __tablename__ = "role_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    base_role: Mapped[str] = mapped_column(String(30))
    permissions: Mapped[list] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(default=1)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(30))
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("operators.id"))
    enabled: Mapped[bool] = mapped_column(default=True)
    must_change_password: Mapped[bool] = mapped_column(default=False, server_default="false")
    allowed_districts: Mapped[list | None] = mapped_column(JSON, nullable=True)
    role_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("role_profiles.id", name="fk_users_role_profile")
    )
    version: Mapped[int] = mapped_column(default=1, server_default="1")


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    csrf: Mapped[str] = mapped_column(String(100))


class Station(Base):
    __tablename__ = "stations"
    __table_args__ = (
        UniqueConstraint("id", "operator_id", "source", name="uq_station_source_owner"),
        UniqueConstraint("source", "external_id"),
        CheckConstraint(
            "(longitude IS NULL AND latitude IS NULL) OR (longitude IS NOT NULL AND latitude IS NOT NULL AND longitude BETWEEN -180 AND 180 AND latitude BETWEEN -90 AND 90)",
            name="ck_station_coordinate_pair",
        ),
        CheckConstraint(
            "coordinate_system IN ('unknown', 'wgs84', 'gcj02', 'bd09')",
            name="ck_station_coordinate_system",
        ),
        CheckConstraint(
            "station_type IN ('unknown', 'charging', 'swapping', 'mixed')", name="ck_station_type"
        ),
        Index("ix_station_location", "coordinate_system", "longitude", "latitude"),
        CheckConstraint(
            "usage_type IN ('unknown','public','personal','bus','sanitation','logistics','taxi','other')",
            name="ck_station_usage_type",
        ),
        CheckConstraint(
            "construction_site IN ('unknown','public_institution','residential','enterprise','office','industrial','transit','culture_sports','highway','green_space','roadside','building_parking','other')",
            name="ck_station_construction_site",
        ),
        CheckConstraint(
            "site_state IN ('unknown','constructing','offline','maintenance','normal')",
            name="ck_station_site_state",
        ),
        CheckConstraint(
            "source_sync_state IN ('unknown','active','removed')",
            name="ck_station_source_sync_state",
        ),
    )
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    coordinate_system: Mapped[str] = mapped_column(
        String(20), default="unknown", server_default="unknown"
    )
    location_is_demo: Mapped[bool] = mapped_column(default=False, server_default="false")
    station_type: Mapped[str] = mapped_column(
        String(20), default="unknown", server_default="unknown"
    )

    usage_type: Mapped[str] = mapped_column(String(30), default="unknown", server_default="unknown")
    construction_site: Mapped[str] = mapped_column(
        String(30), default="unknown", server_default="unknown"
    )
    site_state: Mapped[str] = mapped_column(String(30), default="unknown", server_default="unknown")
    source_sync_state: Mapped[str] = mapped_column(
        String(30), default="unknown", server_default="unknown"
    )
    source_first_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    id: Mapped[int] = mapped_column(primary_key=True)
    operator_id: Mapped[int] = mapped_column(ForeignKey("operators.id"), index=True)
    source: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(150))
    district: Mapped[str] = mapped_column(String(50))
    address: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(40), default="pending")
    connectors: Mapped[int] = mapped_column(default=0)
    power_kw: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    version: Mapped[int] = mapped_column(default=1)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Audit(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_time_id", "created_at", "id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(100))
    object_id: Mapped[int | None]
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Equipment(Base):
    __tablename__ = "equipment"
    __table_args__ = (
        UniqueConstraint("station_id", "external_id"),
        UniqueConstraint("id", "station_id", name="uq_equipment_station_identity"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(150))
    power_kw: Mapped[Decimal] = mapped_column(Numeric(12, 2))


class Connector(Base):
    __tablename__ = "connectors"
    __table_args__ = (UniqueConstraint("equipment_id", "external_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    equipment_id: Mapped[int] = mapped_column(ForeignKey("equipment.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(40), default="unknown")


class SyncTask(Base):
    __tablename__ = "sync_tasks"
    __table_args__ = (UniqueConstraint("source", "request_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(50))
    request_key: Mapped[str] = mapped_column(String(100))
    operator_id: Mapped[int] = mapped_column(ForeignKey("operators.id"))
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=3)
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(300))
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class StationApplication(Base):
    __tablename__ = "station_applications"
    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), index=True)
    operator_id: Mapped[int] = mapped_column(ForeignKey("operators.id"), index=True)
    applicant_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="draft")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(default=1)
    rule_version: Mapped[str] = mapped_column(String(50), default="demo-workflow-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ApprovalAction(Base):
    __tablename__ = "approval_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("station_applications.id"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(30))
    comment: Mapped[str] = mapped_column(String(2000), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LoginThrottle(Base):
    __tablename__ = "login_throttles"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(default=0)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("station_applications.id"), index=True)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    storage_key: Mapped[str] = mapped_column(String(100), unique=True)
    filename: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EnergyRecord(Base):
    __tablename__ = "energy_records"
    __table_args__ = (UniqueConstraint("source", "external_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), index=True)
    source: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str] = mapped_column(String(100))
    day: Mapped[date] = mapped_column(Date)
    kwh: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    eligible: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SubsidyPolicy(Base):
    __tablename__ = "subsidy_policies"
    __table_args__ = (UniqueConstraint("code", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(60))
    version: Mapped[int]
    name: Mapped[str] = mapped_column(String(150))
    rate: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date] = mapped_column(Date)
    enabled: Mapped[bool] = mapped_column(default=True)
    is_demo: Mapped[bool] = mapped_column(default=True)
    device_rules: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SubsidyClaim(Base):
    __tablename__ = "subsidy_claims"
    application_note: Mapped[str | None] = mapped_column(String(2000))
    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), index=True)
    operator_id: Mapped[int] = mapped_column(ForeignKey("operators.id"), index=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey("subsidy_policies.id"))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    total_kwh: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    base_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    adjusted_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    snapshot: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(default=1)
    is_demo: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SubsidyAnnualBatch(Base):
    __tablename__ = "subsidy_annual_batches"
    __table_args__ = (
        UniqueConstraint("actor_id", "request_key", name="uq_subsidy_annual_request"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    operator_id: Mapped[int] = mapped_column(ForeignKey("operators.id"))
    request_key: Mapped[str] = mapped_column(String(36))
    fingerprint: Mapped[str] = mapped_column(String(64))
    receipt: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SubsidyAttachment(Base):
    __tablename__ = "subsidy_attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("subsidy_claims.id"), index=True)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    submitted_version: Mapped[int | None]
    storage_key: Mapped[str] = mapped_column(String(64), unique=True)
    filename: Mapped[str] = mapped_column(String(180))
    content_type: Mapped[str] = mapped_column(String(80))
    size: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EnergyAllocation(Base):
    __tablename__ = "energy_allocations"
    energy_id: Mapped[int] = mapped_column(ForeignKey("energy_records.id"), primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("subsidy_claims.id"), index=True)


class SubsidyAction(Base):
    __tablename__ = "subsidy_actions"
    transition_snapshot: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("subsidy_claims.id"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(30))
    comment: Mapped[str] = mapped_column(String(2000))
    delta: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RatingStandard(Base):
    __tablename__ = "rating_standards"
    __table_args__ = (UniqueConstraint("code", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(60))
    version: Mapped[int]
    name: Mapped[str] = mapped_column(String(150))
    rules: Mapped[dict] = mapped_column(JSON)
    is_demo: Mapped[bool] = mapped_column(default=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    revision: Mapped[int] = mapped_column(default=1, server_default="1")
    archived: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RatingStandardDraft(Base):
    __tablename__ = "rating_standard_drafts"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_rating_standard_draft_version"),
        CheckConstraint(
            "status IN ('draft','published','deleted')", name="ck_rating_standard_draft_status"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    version: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    data: Mapped[dict] = mapped_column(JSON)
    published_standard_id: Mapped[int | None] = mapped_column(ForeignKey("rating_standards.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RatingApplication(Base):
    __tablename__ = "rating_applications"
    __table_args__ = (
        Index(
            "uq_rating_current_publication",
            "station_id",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
    )
    kind: Mapped[str] = mapped_column(String(30), default="initial", server_default="initial")
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("rating_applications.id", name="fk_rating_parent")
    )
    before_result: Mapped[dict | None] = mapped_column(JSON)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[date | None] = mapped_column(Date)
    report_snapshot: Mapped[dict | None] = mapped_column(JSON)

    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), index=True)
    operator_id: Mapped[int] = mapped_column(ForeignKey("operators.id"), index=True)
    standard_id: Mapped[int] = mapped_column(ForeignKey("rating_standards.id"))
    status: Mapped[str] = mapped_column(String(30), default="submitted")
    version: Mapped[int] = mapped_column(default=1)
    initial_scores: Mapped[dict] = mapped_column(JSON)
    current_scores: Mapped[dict] = mapped_column(JSON)
    calculation: Mapped[dict] = mapped_column(JSON)
    rule_snapshot: Mapped[dict] = mapped_column(JSON)
    evidence_note: Mapped[str] = mapped_column(String(2000), default="")
    is_demo: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RatingAction(Base):
    __tablename__ = "rating_actions"
    transition_snapshot: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("rating_applications.id"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(30))
    comment: Mapped[str] = mapped_column(String(2000))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class InspectionTask(Base):
    __tablename__ = "inspection_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), index=True)
    operator_id: Mapped[int] = mapped_column(ForeignKey("operators.id"), index=True)
    title: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(String(4000))
    severity: Mapped[str] = mapped_column(String(20))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="assigned")
    version: Mapped[int] = mapped_column(default=1)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    is_demo: Mapped[bool] = mapped_column(default=True)


class InspectionAction(Base):
    __tablename__ = "inspection_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("inspection_tasks.id"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(30))
    comment: Mapped[str] = mapped_column(String(4000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class InspectionAttachment(Base):
    __tablename__ = "inspection_attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("inspection_tasks.id"), index=True)
    action_id: Mapped[int | None] = mapped_column(ForeignKey("inspection_actions.id"), index=True)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    storage_key: Mapped[str] = mapped_column(String(64), unique=True)
    filename: Mapped[str] = mapped_column(String(180))
    content_type: Mapped[str] = mapped_column(String(80))
    size: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AlertRule(Base):
    __tablename__ = "alert_rules"
    __table_args__ = (UniqueConstraint("code", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(60))
    version: Mapped[int]
    name: Mapped[str] = mapped_column(String(150))
    metric: Mapped[str] = mapped_column(String(60))
    unit: Mapped[str] = mapped_column(String(30))
    trigger_value: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    recover_value: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    notifications_enabled: Mapped[bool] = mapped_column(default=True)
    is_demo: Mapped[bool] = mapped_column(default=True)
    enabled: Mapped[bool] = mapped_column(default=True)


class AlertEvent(Base):
    __tablename__ = "alert_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("alert_rules.id"), index=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), index=True)
    operator_id: Mapped[int] = mapped_column(ForeignKey("operators.id"), index=True)
    target: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="active")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    occurrences: Mapped[int] = mapped_column(default=1)
    last_value: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    rule_snapshot: Mapped[dict] = mapped_column(JSON)
    acknowledged_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledge_note: Mapped[str] = mapped_column(String(2000), default="")
    version: Mapped[int] = mapped_column(default=1)
    is_demo: Mapped[bool] = mapped_column(default=True)


class AlertCursor(Base):
    __tablename__ = "alert_cursors"
    __table_args__ = (UniqueConstraint("rule_id", "station_id", "target"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("alert_rules.id"))
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    target: Mapped[str] = mapped_column(String(100))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active_event_id: Mapped[int | None] = mapped_column(ForeignKey("alert_events.id"))


class AlertNotice(Base):
    __tablename__ = "alert_notices"
    __table_args__ = (UniqueConstraint("event_id", "phase"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("alert_events.id"), index=True)
    phase: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Article(Base):
    __tablename__ = "articles"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(150))
    body: Mapped[str] = mapped_column(String(20000))
    rich_document: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    recipient_ids: Mapped[list | None] = mapped_column(JSON(none_as_null=True))
    audience: Mapped[str] = mapped_column(String(20), default="all")
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("operators.id"))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(default=1)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    is_demo: Mapped[bool] = mapped_column(default=True)


class ArticleAction(Base):
    __tablename__ = "article_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(20))
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ArticleReply(Base):
    __tablename__ = "article_replies"
    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    operator_id: Mapped[int] = mapped_column(ForeignKey("operators.id"))
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(String(4000))
    contact_name: Mapped[str | None] = mapped_column(String(100))
    contact_phone: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Registration(Base):
    __tablename__ = "registrations"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    company_name: Mapped[str] = mapped_column(String(150), unique=True)
    credit_code: Mapped[str] = mapped_column(String(18), unique=True)
    registration_address: Mapped[str] = mapped_column(String(255), default="")
    contacts: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    version: Mapped[int] = mapped_column(default=1)
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("operators.id"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    is_demo: Mapped[bool] = mapped_column(default=True)


class EnrollmentSession(Base):
    __tablename__ = "enrollment_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    registration_id: Mapped[int] = mapped_column(ForeignKey("registrations.id"), index=True)
    csrf: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RegistrationAction(Base):
    __tablename__ = "registration_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    registration_id: Mapped[int] = mapped_column(ForeignKey("registrations.id"), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(30))
    comment: Mapped[str] = mapped_column(String(2000))
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RegistrationAttachment(Base):
    __tablename__ = "registration_attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    registration_id: Mapped[int] = mapped_column(ForeignKey("registrations.id"), index=True)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    storage_key: Mapped[str] = mapped_column(String(64), unique=True)
    filename: Mapped[str] = mapped_column(String(180))
    content_type: Mapped[str] = mapped_column(String(80))
    submitted_version: Mapped[int | None]
    size: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RatingAttachment(Base):
    __tablename__ = "rating_attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("rating_applications.id"), index=True)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    category: Mapped[str] = mapped_column(String(50))
    submitted_version: Mapped[int | None]
    storage_key: Mapped[str] = mapped_column(String(64), unique=True)
    filename: Mapped[str] = mapped_column(String(180))
    content_type: Mapped[str] = mapped_column(String(80))
    size: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ArticleReplyAttachment(Base):
    __tablename__ = "article_reply_attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    reply_id: Mapped[int] = mapped_column(ForeignKey("article_replies.id"), index=True)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    storage_key: Mapped[str] = mapped_column(String(64), unique=True)
    filename: Mapped[str] = mapped_column(String(180))
    content_type: Mapped[str] = mapped_column(String(80))
    size: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ChargingOrder(Base):
    __tablename__ = "charging_orders"
    __table_args__ = (
        UniqueConstraint(
            "source", "operator_id", "external_id", name="uq_order_source_operator_external"
        ),
        CheckConstraint(
            "status IN ('unknown','charging','completed','interrupted','cancelled')",
            name="ck_order_status",
        ),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ck_order_time"),
        CheckConstraint(
            "status != 'completed' OR ended_at IS NOT NULL", name="ck_order_completed_end"
        ),
        CheckConstraint("status != 'charging' OR ended_at IS NULL", name="ck_order_charging_end"),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0", name="ck_order_duration"
        ),
        CheckConstraint("kwh IS NULL OR (kwh >= 0 AND kwh != 'NaN'::numeric)", name="ck_order_kwh"),
        CheckConstraint(
            "amount IS NULL OR (amount >= 0 AND amount != 'NaN'::numeric AND currency IS NOT NULL AND currency = 'CNY')",
            name="ck_order_amount",
        ),
        CheckConstraint("currency IS NULL OR currency = 'CNY'", name="ck_order_currency"),
        Index("ix_order_station_started_id", "station_id", "started_at", "id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    operator_id: Mapped[int] = mapped_column(ForeignKey("operators.id"))
    source: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str] = mapped_column(String(100))
    equipment_external_id: Mapped[str | None] = mapped_column(String(100))
    connector_external_id: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None]
    kwh: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    version: Mapped[int] = mapped_column(default=1)
    is_demo: Mapped[bool] = mapped_column(default=False)


class HourlyEnergyRecord(Base):
    __tablename__ = "hourly_energy_records"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "operator_id",
            "station_id",
            "meter_scope",
            "equipment_external_id",
            "started_at",
            name="uq_hourly_energy_slot",
        ),
        CheckConstraint(
            "(meter_scope = 'station' AND equipment_external_id = '') OR (meter_scope = 'equipment' AND equipment_external_id != '')",
            name="ck_hourly_energy_scope",
        ),
        CheckConstraint(
            "ended_at = started_at + interval '1 hour'", name="ck_hourly_energy_interval"
        ),
        CheckConstraint(
            "EXTRACT(MINUTE FROM started_at AT TIME ZONE 'Asia/Shanghai') = 0 AND EXTRACT(SECOND FROM started_at AT TIME ZONE 'Asia/Shanghai') = 0",
            name="ck_hourly_energy_alignment",
        ),
        CheckConstraint(
            "kwh IS NULL OR (kwh >= 0 AND kwh != 'NaN'::numeric)", name="ck_hourly_energy_kwh"
        ),
        Index("ix_hourly_energy_station_time", "station_id", "started_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    operator_id: Mapped[int] = mapped_column(ForeignKey("operators.id"))
    source: Mapped[str] = mapped_column(String(50))
    meter_scope: Mapped[str] = mapped_column(String(20))
    equipment_external_id: Mapped[str] = mapped_column(String(100), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kwh: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    version: Mapped[int] = mapped_column(default=1)
    is_demo: Mapped[bool] = mapped_column(default=False)


class FaultEvent(Base):
    """Source event lifecycle; independent from alert acknowledgement and connector snapshots."""

    __tablename__ = "fault_events"
    __table_args__ = (
        UniqueConstraint(
            "source", "operator_id", "external_id", name="uq_fault_source_operator_external"
        ),
        CheckConstraint("status IN ('unknown','active','recovered')", name="ck_fault_status"),
        CheckConstraint(
            "severity IN ('unknown','info','warning','critical')", name="ck_fault_severity"
        ),
        CheckConstraint(
            "(status = 'recovered' AND recovered_at IS NOT NULL AND recovered_at >= started_at) OR (status != 'recovered' AND recovered_at IS NULL)",
            name="ck_fault_lifecycle",
        ),
        CheckConstraint(
            "connector_external_id IS NULL OR equipment_external_id IS NOT NULL",
            name="ck_fault_reference",
        ),
        Index("ix_fault_station_started_id", "station_id", "started_at", "id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    operator_id: Mapped[int] = mapped_column(ForeignKey("operators.id"))
    source: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str] = mapped_column(String(100))
    equipment_external_id: Mapped[str | None] = mapped_column(String(100))
    connector_external_id: Mapped[str | None] = mapped_column(String(100))
    fault_code: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))
    severity: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    version: Mapped[int] = mapped_column(default=1)
    is_demo: Mapped[bool] = mapped_column(default=False)


class RuntimeInterval(Base):
    """Finite source observations on two independent axes, never extrapolated snapshots."""

    __tablename__ = "runtime_intervals"
    __table_args__ = (
        UniqueConstraint(
            "source", "operator_id", "external_id", name="uq_runtime_source_operator_external"
        ),
        CheckConstraint(
            "ended_at > started_at AND ended_at <= source_updated_at AND isfinite(started_at) AND isfinite(ended_at) AND isfinite(source_updated_at)",
            name="ck_runtime_time",
        ),
        CheckConstraint("length(trim(equipment_external_id)) > 0", name="ck_runtime_equipment"),
        CheckConstraint(
            "(dimension = 'connectivity' AND state IN ('online','offline','unknown')) OR (dimension = 'operation' AND state IN ('available','occupied','charging','faulted','maintenance','unknown'))",
            name="ck_runtime_state",
        ),
        ExcludeConstraint(
            ("source", "="),
            ("operator_id", "="),
            ("station_id", "="),
            ("equipment_external_id", "="),
            ("connector_external_id", "="),
            ("dimension", "="),
            ("is_demo", "="),
            (text("tstzrange(started_at, ended_at, '[)')"), "&&"),
            name="ex_runtime_no_overlap",
            using="gist",
            deferrable=True,
            initially="IMMEDIATE",
            ops={
                "source": "public.gist_text_ops",
                "operator_id": "public.gist_int4_ops",
                "station_id": "public.gist_int4_ops",
                "equipment_external_id": "public.gist_text_ops",
                "connector_external_id": "public.gist_text_ops",
                "dimension": "public.gist_text_ops",
                "is_demo": "public.gist_bool_ops",
            },
        ),
        Index("ix_runtime_station_started_id", "station_id", "started_at", "id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    operator_id: Mapped[int] = mapped_column(ForeignKey("operators.id"))
    source: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str] = mapped_column(String(100))
    equipment_external_id: Mapped[str] = mapped_column(String(100))
    connector_external_id: Mapped[str] = mapped_column(String(100), default="")
    dimension: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    version: Mapped[int] = mapped_column(default=1)
    is_demo: Mapped[bool] = mapped_column(default=False)


class ArticleAttachment(Base):
    __tablename__ = "article_attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    filename: Mapped[str] = mapped_column(String(180))
    storage_key: Mapped[str] = mapped_column(String(48), unique=True)
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    frozen: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class NoticeDelivery(Base):
    __tablename__ = "notice_deliveries"
    __table_args__ = (UniqueConstraint("article_id", "operator_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    operator_id: Mapped[int] = mapped_column(ForeignKey("operators.id"), index=True)
    operator_name: Mapped[str] = mapped_column(String(150))
    contacts: Mapped[list] = mapped_column(JSON)
    publication_version: Mapped[int] = mapped_column()
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CaptchaChallenge(Base):
    __tablename__ = "captcha_challenges"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    binding_hash: Mapped[str] = mapped_column(String(64), index=True)
    answer_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VerifiedPhone(Base):
    __tablename__ = "verified_phones"
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    phone: Mapped[str] = mapped_column(String(11), unique=True)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    verification_mode: Mapped[str] = mapped_column(String(20), default="demo")
    version: Mapped[int] = mapped_column(default=1)


class SmsChallenge(Base):
    __tablename__ = "sms_challenges"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    binding_hash: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    phone: Mapped[str | None] = mapped_column(String(11))
    subject_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    phone_version: Mapped[int | None] = mapped_column()
    purpose: Mapped[str] = mapped_column(String(20))
    code_hash: Mapped[str] = mapped_column(String(64))
    credential_version: Mapped[int | None] = mapped_column()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_mode: Mapped[str] = mapped_column(String(20))
    receipt_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class StaffApplication(Base):
    __tablename__ = "staff_applications"
    __table_args__ = (
        Index(
            "uq_staff_pending_contact",
            "requested_role",
            "contact_phone",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    requested_role: Mapped[str] = mapped_column(String(30))
    applicant_name: Mapped[str] = mapped_column(String(100))
    contact_phone: Mapped[str] = mapped_column(String(11))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_comment: Mapped[str | None] = mapped_column(String(1000))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    username: Mapped[str | None] = mapped_column(String(100))
    grant_snapshot: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    is_demo: Mapped[bool] = mapped_column(default=True)


class StaffApplicationAction(Base):
    __tablename__ = "staff_application_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("staff_applications.id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(20))
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class StaffApplicantSession(Base):
    __tablename__ = "staff_applicant_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("staff_applications.id", ondelete="CASCADE"), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SubsidyDeviceDay(Base):
    """Independent source device-day evidence, never a financial eligibility flag."""

    __tablename__ = "subsidy_device_days"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "station_id", "operator_id", "source", "day", "scope_external_id"],
            [
                "subsidy_device_scopes.id",
                "subsidy_device_scopes.station_id",
                "subsidy_device_scopes.operator_id",
                "subsidy_device_scopes.source",
                "subsidy_device_scopes.day",
                "subsidy_device_scopes.external_id",
            ],
            name="fk_device_day_scope_identity",
        ),
        CheckConstraint(
            "(scope_id IS NULL AND scope_external_id IS NULL) OR (scope_id IS NOT NULL AND scope_external_id IS NOT NULL)",
            name="ck_device_day_scope_pair",
        ),
        UniqueConstraint(
            "source", "operator_id", "external_id", name="uq_subsidy_device_source_record"
        ),
        UniqueConstraint(
            "source", "station_id", "equipment_id", "day", name="uq_subsidy_device_day"
        ),
        ForeignKeyConstraint(
            ["station_id", "operator_id", "source"],
            ["stations.id", "stations.operator_id", "stations.source"],
            name="fk_subsidy_device_source_owner",
        ),
        ForeignKeyConstraint(
            ["equipment_id", "station_id"],
            ["equipment.id", "equipment.station_id"],
            name="fk_subsidy_device_equipment_station",
        ),
        CheckConstraint(
            "business_kind IN ('charging','swapping','unknown')", name="ck_subsidy_device_kind"
        ),
        CheckConstraint(
            "metric_basis IN ('calendar_day_seconds_v1','unconfirmed')",
            name="ck_subsidy_device_basis",
        ),
        CheckConstraint(
            "equipment_type IN ('dc','ac','swapping','unknown')", name="ck_subsidy_device_type"
        ),
        CheckConstraint("version >= 1", name="ck_subsidy_device_version"),
        CheckConstraint(
            "NOT energy_complete OR energy_kwh IS NOT NULL",
            name="ck_subsidy_device_energy_complete",
        ),
        CheckConstraint(
            "production_date IS NULL OR production_date <= day",
            name="ck_subsidy_device_production_day",
        ),
        *(
            CheckConstraint(
                f"{field} IS NULL OR ({field} >= 0 AND {field} != 'NaN'::numeric)",
                name=f"ck_device_day_{field}",
            )
            for field in ["energy_kwh", "power_kw"]
        ),
        *(
            CheckConstraint(
                f"({axis}_observed_seconds IS NULL AND {axis}_unknown_seconds IS NULL AND {axis}_seconds IS NULL) OR ({axis}_observed_seconds IS NOT NULL AND {axis}_unknown_seconds IS NOT NULL AND {axis}_seconds IS NOT NULL AND {axis}_observed_seconds >= 0 AND {axis}_observed_seconds <= 86400 AND {axis}_unknown_seconds >= 0 AND {axis}_seconds >= 0 AND {axis}_unknown_seconds + {axis}_seconds <= {axis}_observed_seconds)",
                name=f"ck_device_day_{axis}_coverage",
            )
            for axis in ["fault", "offline"]
        ),
        Index("ix_subsidy_device_station_day", "station_id", "day"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    scope_id: Mapped[int | None]
    scope_external_id: Mapped[str | None] = mapped_column(String(100))
    station_id: Mapped[int]
    operator_id: Mapped[int]
    equipment_id: Mapped[int]
    source: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str] = mapped_column(String(100))
    equipment_external_id: Mapped[str] = mapped_column(String(100))
    day: Mapped[date] = mapped_column(Date)
    equipment_name: Mapped[str | None] = mapped_column(String(150))
    equipment_type: Mapped[str] = mapped_column(String(20))
    power_kw: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    production_date: Mapped[date | None] = mapped_column(Date)
    business_kind: Mapped[str] = mapped_column(String(20))
    energy_kwh: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    energy_complete: Mapped[bool]
    metric_basis: Mapped[str] = mapped_column(String(40))
    fault_observed_seconds: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    fault_unknown_seconds: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    fault_seconds: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    offline_observed_seconds: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    offline_unknown_seconds: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    offline_seconds: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    version: Mapped[int] = mapped_column(default=1)
    is_demo: Mapped[bool]


class SubsidyDeviceScope(Base):
    __tablename__ = "subsidy_device_scopes"
    __table_args__ = (
        UniqueConstraint("source", "operator_id", "external_id", name="uq_device_scope_source"),
        UniqueConstraint("source", "station_id", "day", name="uq_device_scope_day"),
        UniqueConstraint(
            "id",
            "station_id",
            "operator_id",
            "source",
            "day",
            "external_id",
            name="uq_device_scope_identity",
        ),
        ForeignKeyConstraint(
            ["station_id", "operator_id", "source"],
            ["stations.id", "stations.operator_id", "stations.source"],
            name="fk_device_scope_owner",
        ),
        CheckConstraint("version >= 1", name="ck_device_scope_version"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int]
    operator_id: Mapped[int]
    source: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str] = mapped_column(String(100))
    day: Mapped[date] = mapped_column(Date)
    equipment_external_ids: Mapped[list] = mapped_column(JSON)
    complete: Mapped[bool]
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    version: Mapped[int] = mapped_column(default=1)
    is_demo: Mapped[bool]


class SubsidyDeviceLine(Base):
    __tablename__ = "subsidy_device_lines"
    claim_id: Mapped[int] = mapped_column(ForeignKey("subsidy_claims.id"), primary_key=True)
    fact_id: Mapped[int] = mapped_column(ForeignKey("subsidy_device_days.id"), primary_key=True)
    snapshot: Mapped[dict] = mapped_column(JSON)


class SubsidyDeviceAllocation(Base):
    __tablename__ = "subsidy_device_allocations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["claim_id", "fact_id"],
            ["subsidy_device_lines.claim_id", "subsidy_device_lines.fact_id"],
            name="fk_device_allocation_frozen_line",
        ),
    )
    fact_id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int]
