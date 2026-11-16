"""Independent source-reported fault lifecycle, not alert or repair workflow."""

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "fault_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("station_id", sa.Integer(), sa.ForeignKey("stations.id"), nullable=False),
        sa.Column("operator_id", sa.Integer(), sa.ForeignKey("operators.id"), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("external_id", sa.String(100), nullable=False),
        sa.Column("equipment_external_id", sa.String(100)),
        sa.Column("connector_external_id", sa.String(100)),
        sa.Column("fault_code", sa.String(100)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recovered_at", sa.DateTime(timezone=True)),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.UniqueConstraint(
            "source", "operator_id", "external_id", name="uq_fault_source_operator_external"
        ),
        sa.CheckConstraint("status IN ('unknown','active','recovered')", name="ck_fault_status"),
        sa.CheckConstraint(
            "severity IN ('unknown','info','warning','critical')", name="ck_fault_severity"
        ),
        sa.CheckConstraint(
            "(status = 'recovered' AND recovered_at IS NOT NULL AND recovered_at >= started_at) OR (status != 'recovered' AND recovered_at IS NULL)",
            name="ck_fault_lifecycle",
        ),
        sa.CheckConstraint(
            "connector_external_id IS NULL OR equipment_external_id IS NOT NULL",
            name="ck_fault_reference",
        ),
    )
    op.create_index(
        "ix_fault_station_started_id", "fault_events", ["station_id", "started_at", "id"]
    )


def downgrade():
    op.drop_table("fault_events")
