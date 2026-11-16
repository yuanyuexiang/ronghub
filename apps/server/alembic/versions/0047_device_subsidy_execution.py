"""Source-declared day cohorts and recoverable device claim lines/allocations."""

import sqlalchemy as sa

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "subsidy_device_scopes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("station_id", sa.Integer(), nullable=False),
        sa.Column("operator_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("external_id", sa.String(100), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("equipment_external_ids", sa.JSON(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("source", "operator_id", "external_id", name="uq_device_scope_source"),
        sa.UniqueConstraint("source", "station_id", "day", name="uq_device_scope_day"),
        sa.UniqueConstraint(
            "id",
            "station_id",
            "operator_id",
            "source",
            "day",
            "external_id",
            name="uq_device_scope_identity",
        ),
        sa.ForeignKeyConstraint(
            ["station_id", "operator_id", "source"],
            ["stations.id", "stations.operator_id", "stations.source"],
            name="fk_device_scope_owner",
        ),
        sa.CheckConstraint("version >= 1", name="ck_device_scope_version"),
    )
    op.add_column("subsidy_device_days", sa.Column("scope_id", sa.Integer(), nullable=True))
    op.add_column(
        "subsidy_device_days", sa.Column("scope_external_id", sa.String(100), nullable=True)
    )
    op.create_foreign_key(
        "fk_device_day_scope_identity",
        "subsidy_device_days",
        "subsidy_device_scopes",
        ["scope_id", "station_id", "operator_id", "source", "day", "scope_external_id"],
        ["id", "station_id", "operator_id", "source", "day", "external_id"],
    )
    op.create_check_constraint(
        "ck_device_day_scope_pair",
        "subsidy_device_days",
        "(scope_id IS NULL AND scope_external_id IS NULL) OR (scope_id IS NOT NULL AND scope_external_id IS NOT NULL)",
    )
    op.create_table(
        "subsidy_device_lines",
        sa.Column("claim_id", sa.Integer(), sa.ForeignKey("subsidy_claims.id"), primary_key=True),
        sa.Column(
            "fact_id", sa.Integer(), sa.ForeignKey("subsidy_device_days.id"), primary_key=True
        ),
        sa.Column("snapshot", sa.JSON(), nullable=False),
    )
    op.create_table(
        "subsidy_device_allocations",
        sa.Column("fact_id", sa.Integer(), primary_key=True),
        sa.Column("claim_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["claim_id", "fact_id"],
            ["subsidy_device_lines.claim_id", "subsidy_device_lines.fact_id"],
            name="fk_device_allocation_frozen_line",
        ),
    )


def downgrade():
    connection = op.get_bind()
    if any(
        connection.scalar(sa.text(f"SELECT count(*) FROM {table}"))
        for table in ["subsidy_device_scopes", "subsidy_device_lines", "subsidy_device_allocations"]
    ):
        raise RuntimeError("Refuse downgrade: device scope/claim evidence would be lost")
    op.drop_table("subsidy_device_allocations")
    op.drop_table("subsidy_device_lines")
    op.drop_constraint("ck_device_day_scope_pair", "subsidy_device_days", type_="check")
    op.drop_constraint("fk_device_day_scope_identity", "subsidy_device_days", type_="foreignkey")
    op.drop_column("subsidy_device_days", "scope_external_id")
    op.drop_column("subsidy_device_days", "scope_id")
    op.drop_table("subsidy_device_scopes")
