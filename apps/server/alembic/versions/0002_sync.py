"""Equipment, connectors and durable synchronization tasks."""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "equipment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("station_id", sa.Integer(), sa.ForeignKey("stations.id"), nullable=False),
        sa.Column("external_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("power_kw", sa.Numeric(12, 2), nullable=False),
        sa.UniqueConstraint("station_id", "external_id"),
    )
    op.create_index("ix_equipment_station_id", "equipment", ["station_id"])
    op.create_table(
        "connectors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("equipment_id", sa.Integer(), sa.ForeignKey("equipment.id"), nullable=False),
        sa.Column("external_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.UniqueConstraint("equipment_id", "external_id"),
    )
    op.create_index("ix_connectors_equipment_id", "connectors", ["equipment_id"])
    op.create_table(
        "sync_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("request_key", sa.String(100), nullable=False),
        sa.Column("operator_id", sa.Integer(), sa.ForeignKey("operators.id"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(100)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.String(300)),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.UniqueConstraint("source", "request_key"),
    )
    op.create_index("ix_sync_tasks_status", "sync_tasks", ["status"])


def downgrade():
    op.drop_table("sync_tasks")
    op.drop_table("connectors")
    op.drop_table("equipment")
