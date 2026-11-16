"""Durable receipts for atomic annual multi-station subsidy creation."""

import sqlalchemy as sa

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "subsidy_annual_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("operator_id", sa.Integer(), sa.ForeignKey("operators.id"), nullable=False),
        sa.Column("request_key", sa.String(36), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("actor_id", "request_key", name="uq_subsidy_annual_request"),
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM subsidy_annual_batches")):
        raise RuntimeError("Refuse downgrade: durable annual subsidy receipts would be lost")
    op.drop_table("subsidy_annual_batches")
