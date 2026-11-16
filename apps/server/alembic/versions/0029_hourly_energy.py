"""Source-reported aligned hourly energy, never apportioned from order totals."""

import sqlalchemy as sa

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "hourly_energy_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("station_id", sa.Integer(), sa.ForeignKey("stations.id"), nullable=False),
        sa.Column("operator_id", sa.Integer(), sa.ForeignKey("operators.id"), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("meter_scope", sa.String(20), nullable=False),
        sa.Column("equipment_external_id", sa.String(100), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kwh", sa.Numeric(18, 6)),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.UniqueConstraint(
            "source",
            "operator_id",
            "station_id",
            "meter_scope",
            "equipment_external_id",
            "started_at",
            name="uq_hourly_energy_slot",
        ),
        sa.CheckConstraint(
            "(meter_scope = 'station' AND equipment_external_id = '') OR (meter_scope = 'equipment' AND equipment_external_id != '')",
            name="ck_hourly_energy_scope",
        ),
        sa.CheckConstraint(
            "ended_at = started_at + interval '1 hour'", name="ck_hourly_energy_interval"
        ),
        sa.CheckConstraint(
            "EXTRACT(MINUTE FROM started_at AT TIME ZONE 'Asia/Shanghai') = 0 AND EXTRACT(SECOND FROM started_at AT TIME ZONE 'Asia/Shanghai') = 0",
            name="ck_hourly_energy_alignment",
        ),
        sa.CheckConstraint(
            "kwh IS NULL OR (kwh >= 0 AND kwh != 'NaN'::numeric)", name="ck_hourly_energy_kwh"
        ),
    )
    op.create_index(
        "ix_hourly_energy_station_time", "hourly_energy_records", ["station_id", "started_at"]
    )


def downgrade():
    op.drop_table("hourly_energy_records")
