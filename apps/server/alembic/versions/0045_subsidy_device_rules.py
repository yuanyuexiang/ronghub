"""Keep complete device-rule configurations without rewriting legacy policies."""

import sqlalchemy as sa

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("subsidy_policies", sa.Column("device_rules", sa.JSON(), nullable=True))


def downgrade():
    # Refuse losing any explicit new configuration, including unknown future shapes.
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM subsidy_policies WHERE device_rules IS NOT NULL")
    ):
        raise RuntimeError("Refuse downgrade: device subsidy configurations would be lost")
    op.drop_column("subsidy_policies", "device_rules")
