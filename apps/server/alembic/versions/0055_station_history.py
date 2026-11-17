"""Store source first reporting independently of connection/approval clocks."""

import sqlalchemy as sa

from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "stations", sa.Column("source_first_reported_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "ix_energy_station_day_source", "energy_records", ["station_id", "day", "source"]
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS(SELECT 1 FROM stations WHERE source_first_reported_at IS NOT NULL)")
    ):
        raise RuntimeError("Refuse downgrade: source first reporting timestamps would be lost")
    op.drop_index("ix_energy_station_day_source", table_name="energy_records")
    op.drop_column("stations", "source_first_reported_at")
