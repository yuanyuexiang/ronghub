"""Recoverable standard drafts and versioned availability controls."""

import sqlalchemy as sa
from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "rating_standards", sa.Column("revision", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column(
        "rating_standards",
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "rating_standard_drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column(
            "published_standard_id",
            sa.Integer(),
            sa.ForeignKey("rating_standards.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_rating_standard_draft_version"),
        sa.CheckConstraint(
            "status IN ('draft','published','deleted')", name="ck_rating_standard_draft_status"
        ),
    )


def downgrade():
    connection = op.get_bind()
    if connection.scalar(
        sa.text("SELECT count(*) FROM rating_standard_drafts")
    ) or connection.scalar(
        sa.text("SELECT count(*) FROM rating_standards WHERE revision <> 1 OR archived")
    ):
        raise RuntimeError(
            "Refuse downgrade: standard drafts or availability history would be lost"
        )
    op.drop_table("rating_standard_drafts")
    op.drop_column("rating_standards", "archived")
    op.drop_column("rating_standards", "revision")
