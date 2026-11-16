"""Independent device-day evidence with database-enforced source/device ownership."""

import sqlalchemy as sa

from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "uq_station_source_owner", "stations", ["id", "operator_id", "source"]
    )
    op.create_unique_constraint("uq_equipment_station_identity", "equipment", ["id", "station_id"])
    columns = [sa.Column("id", sa.Integer(), primary_key=True)]
    columns += [
        sa.Column(key, sa.Integer(), nullable=False)
        for key in ["station_id", "operator_id", "equipment_id"]
    ]
    columns += [
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("external_id", sa.String(100), nullable=False),
        sa.Column("equipment_external_id", sa.String(100), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("equipment_name", sa.String(150)),
        sa.Column("equipment_type", sa.String(20), nullable=False),
        sa.Column("power_kw", sa.Numeric(12, 2)),
        sa.Column("production_date", sa.Date()),
        sa.Column("business_kind", sa.String(20), nullable=False),
        sa.Column("energy_kwh", sa.Numeric(18, 6)),
        sa.Column("energy_complete", sa.Boolean(), nullable=False),
        sa.Column("metric_basis", sa.String(40), nullable=False),
    ]
    columns += [
        sa.Column(f"{axis}_{part}", sa.Numeric(12, 6))
        for axis in ["fault", "offline"]
        for part in ["observed_seconds", "unknown_seconds", "seconds"]
    ]
    columns += [
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
    ]
    op.create_table(
        "subsidy_device_days",
        *columns,
        sa.UniqueConstraint(
            "source", "operator_id", "external_id", name="uq_subsidy_device_source_record"
        ),
        sa.UniqueConstraint(
            "source", "station_id", "equipment_id", "day", name="uq_subsidy_device_day"
        ),
        sa.ForeignKeyConstraint(
            ["station_id", "operator_id", "source"],
            ["stations.id", "stations.operator_id", "stations.source"],
            name="fk_subsidy_device_source_owner",
        ),
        sa.ForeignKeyConstraint(
            ["equipment_id", "station_id"],
            ["equipment.id", "equipment.station_id"],
            name="fk_subsidy_device_equipment_station",
        ),
        sa.CheckConstraint(
            "business_kind IN ('charging','swapping','unknown')", name="ck_subsidy_device_kind"
        ),
        sa.CheckConstraint(
            "metric_basis IN ('calendar_day_seconds_v1','unconfirmed')",
            name="ck_subsidy_device_basis",
        ),
        sa.CheckConstraint(
            "equipment_type IN ('dc','ac','swapping','unknown')", name="ck_subsidy_device_type"
        ),
        sa.CheckConstraint("version >= 1", name="ck_subsidy_device_version"),
        sa.CheckConstraint(
            "NOT energy_complete OR energy_kwh IS NOT NULL",
            name="ck_subsidy_device_energy_complete",
        ),
        sa.CheckConstraint(
            "production_date IS NULL OR production_date <= day",
            name="ck_subsidy_device_production_day",
        ),
        *(
            sa.CheckConstraint(
                f"{field} IS NULL OR ({field} >= 0 AND {field} != 'NaN'::numeric)",
                name=f"ck_device_day_{field}",
            )
            for field in ["energy_kwh", "power_kw"]
        ),
        *(
            sa.CheckConstraint(
                f"({axis}_observed_seconds IS NULL AND {axis}_unknown_seconds IS NULL AND {axis}_seconds IS NULL) OR ({axis}_observed_seconds IS NOT NULL AND {axis}_unknown_seconds IS NOT NULL AND {axis}_seconds IS NOT NULL AND {axis}_observed_seconds >= 0 AND {axis}_observed_seconds <= 86400 AND {axis}_unknown_seconds >= 0 AND {axis}_seconds >= 0 AND {axis}_unknown_seconds + {axis}_seconds <= {axis}_observed_seconds)",
                name=f"ck_device_day_{axis}_coverage",
            )
            for axis in ["fault", "offline"]
        ),
    )
    op.create_index("ix_subsidy_device_station_day", "subsidy_device_days", ["station_id", "day"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM subsidy_device_days")):
        raise RuntimeError("Refuse downgrade: source device-day evidence would be lost")
    op.drop_table("subsidy_device_days")
    op.drop_constraint("uq_equipment_station_identity", "equipment", type_="unique")
    op.drop_constraint("uq_station_source_owner", "stations", type_="unique")
