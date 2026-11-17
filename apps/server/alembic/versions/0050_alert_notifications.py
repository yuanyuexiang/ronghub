"""Private notification settings, recipients and immutable simulated dispatch history."""

import sqlalchemy as sa

from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "alert_notification_preferences",
        sa.Column("category", sa.String(30), primary_key=True),
        sa.Column("in_app_enabled", sa.Boolean(), nullable=False),
        sa.Column("sms_enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version>=1", name="ck_alert_preference_version"),
    )
    op.create_table(
        "alert_recipients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("phone", sa.String(11), nullable=False),
        sa.Column("categories", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version>=1", name="ck_alert_recipient_version"),
        sa.CheckConstraint("phone ~ '^1[3-9][0-9]{9}$'", name="ck_alert_recipient_phone"),
    )
    op.create_index(
        "uq_active_alert_recipient_phone",
        "alert_recipients",
        ["phone"],
        unique=True,
        postgresql_where=sa.text("NOT deleted"),
    )
    op.create_table(
        "alert_notification_routings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id", sa.Integer(), sa.ForeignKey("alert_events.id"), nullable=False, index=True
        ),
        sa.Column("phase", sa.String(20), nullable=False),
        sa.Column("category", sa.String(30), nullable=True),
        sa.Column("preference_version", sa.Integer(), nullable=True),
        sa.Column("in_app_enabled", sa.Boolean(), nullable=False),
        sa.Column("sms_enabled", sa.Boolean(), nullable=False),
        sa.Column("sms_status", sa.String(30), nullable=False),
        sa.Column("notice_id", sa.Integer(), sa.ForeignKey("alert_notices.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_id", "phase", name="uq_alert_routing_phase"),
    )
    op.create_table(
        "alert_sms_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "routing_id",
            sa.Integer(),
            sa.ForeignKey("alert_notification_routings.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "recipient_id", sa.Integer(), sa.ForeignKey("alert_recipients.id"), nullable=False
        ),
        sa.Column("recipient_version", sa.Integer(), nullable=False),
        sa.Column("name_snapshot", sa.String(80), nullable=False),
        sa.Column("phone_snapshot", sa.String(11), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("routing_id", "recipient_id", name="uq_alert_sms_recipient"),
    )


def downgrade():
    db = op.get_bind()
    if any(
        db.scalar(sa.text(f"SELECT count(*) FROM {table}"))
        for table in [
            "alert_notification_preferences",
            "alert_recipients",
            "alert_notification_routings",
            "alert_sms_deliveries",
        ]
    ) or db.scalar(
        sa.text(
            "SELECT count(*) FROM role_profiles WHERE permissions::jsonb ? 'alerts.notifications'"
        )
    ):
        raise RuntimeError(
            "Refuse downgrade: alert notification data or explicit grants would be lost"
        )
    for table in [
        "alert_sms_deliveries",
        "alert_notification_routings",
        "alert_recipients",
        "alert_notification_preferences",
    ]:
        op.drop_table(table)
