"""Source runtime intervals with database-enforced non-overlap."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ExcludeConstraint

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade():
    # Owner-only prerequisite; explicit public opclasses work in isolated verification schemas.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist WITH SCHEMA public")
    op.create_table(
        "runtime_intervals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("station_id", sa.Integer(), sa.ForeignKey("stations.id"), nullable=False),
        sa.Column("operator_id", sa.Integer(), sa.ForeignKey("operators.id"), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("external_id", sa.String(100), nullable=False),
        sa.Column("equipment_external_id", sa.String(100), nullable=False),
        sa.Column("connector_external_id", sa.String(100), nullable=False),
        sa.Column("dimension", sa.String(20), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.UniqueConstraint(
            "source", "operator_id", "external_id", name="uq_runtime_source_operator_external"
        ),
        sa.CheckConstraint(
            "ended_at > started_at AND ended_at <= source_updated_at AND isfinite(started_at) AND isfinite(ended_at) AND isfinite(source_updated_at)",
            name="ck_runtime_time",
        ),
        sa.CheckConstraint("length(trim(equipment_external_id)) > 0", name="ck_runtime_equipment"),
        sa.CheckConstraint(
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
            (sa.text("tstzrange(started_at, ended_at, '[)')"), "&&"),
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
    )
    op.create_index(
        "ix_runtime_station_started_id", "runtime_intervals", ["station_id", "started_at", "id"]
    )


def downgrade():
    op.drop_table("runtime_intervals")
    # Shared extension may be used by other schemas; do not drop it.
