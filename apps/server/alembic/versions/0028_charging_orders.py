"""Source-reported charging orders, independent of daily energy and settlement."""

import sqlalchemy as sa

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "charging_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("station_id", sa.Integer(), sa.ForeignKey("stations.id"), nullable=False),
        sa.Column("operator_id", sa.Integer(), sa.ForeignKey("operators.id"), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("external_id", sa.String(100), nullable=False),
        sa.Column("equipment_external_id", sa.String(100)),
        sa.Column("connector_external_id", sa.String(100)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("duration_seconds", sa.Integer()),
        sa.Column("kwh", sa.Numeric(18, 6)),
        sa.Column("amount", sa.Numeric(18, 2)),
        sa.Column("currency", sa.String(3)),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.UniqueConstraint(
            "source", "operator_id", "external_id", name="uq_order_source_operator_external"
        ),
        sa.CheckConstraint(
            "status IN ('unknown','charging','completed','interrupted','cancelled')",
            name="ck_order_status",
        ),
        sa.CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ck_order_time"),
        sa.CheckConstraint(
            "status != 'completed' OR ended_at IS NOT NULL", name="ck_order_completed_end"
        ),
        sa.CheckConstraint(
            "status != 'charging' OR ended_at IS NULL", name="ck_order_charging_end"
        ),
        sa.CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0", name="ck_order_duration"
        ),
        sa.CheckConstraint(
            "kwh IS NULL OR (kwh >= 0 AND kwh != 'NaN'::numeric)", name="ck_order_kwh"
        ),
        sa.CheckConstraint(
            "amount IS NULL OR (amount >= 0 AND amount != 'NaN'::numeric AND currency IS NOT NULL AND currency = 'CNY')",
            name="ck_order_amount",
        ),
        sa.CheckConstraint("currency IS NULL OR currency = 'CNY'", name="ck_order_currency"),
    )
    op.create_index(
        "ix_order_station_started_id", "charging_orders", ["station_id", "started_at", "id"]
    )


def downgrade():
    op.drop_table("charging_orders")
