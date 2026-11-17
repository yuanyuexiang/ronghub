"""Durable independent configured observations and frozen event evidence."""

import sqlalchemy as sa

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "alert_events",
        sa.Column("configuration_evidence", sa.JSON(none_as_null=True), nullable=True),
    )
    op.create_table(
        "configured_alert_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("namespace", sa.String(80), nullable=False),
        sa.Column("request_key", sa.String(36), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "station_id", sa.Integer(), sa.ForeignKey("stations.id"), nullable=False, index=True
        ),
        sa.Column("operator_id", sa.Integer(), sa.ForeignKey("operators.id"), nullable=False),
        sa.Column(
            "rule_id", sa.Integer(), sa.ForeignKey("alert_rules.id"), nullable=False, index=True
        ),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("alert_events.id"), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.UniqueConstraint("namespace", "request_key", name="uq_configured_observation_request"),
    )


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT count(*) FROM configured_alert_observations")) or bind.scalar(
        sa.text("SELECT count(*) FROM alert_events WHERE configuration_evidence IS NOT NULL")
    ):
        raise RuntimeError(
            "Refuse downgrade: configured alert observation or event evidence would be lost"
        )
    op.drop_table("configured_alert_observations")
    op.drop_column("alert_events", "configuration_evidence")
