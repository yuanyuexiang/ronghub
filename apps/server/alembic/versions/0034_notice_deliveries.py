"""Explicit notice recipients and per-company publication-time snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("articles", sa.Column("recipient_ids", sa.JSON(none_as_null=True), nullable=True))
    op.create_table(
        "notice_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "article_id",
            sa.Integer(),
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("operator_id", sa.Integer(), sa.ForeignKey("operators.id"), nullable=False),
        sa.Column("operator_name", sa.String(150), nullable=False),
        sa.Column("contacts", sa.JSON(), nullable=False),
        sa.Column("publication_version", sa.Integer(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("article_id", "operator_id"),
    )
    op.create_index("ix_notice_deliveries_article_id", "notice_deliveries", ["article_id"])
    op.create_index("ix_notice_deliveries_operator_id", "notice_deliveries", ["operator_id"])


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM notice_deliveries")
    ) or op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM articles WHERE recipient_ids IS NOT NULL AND json_typeof(recipient_ids) != 'null'"
        )
    ):
        raise RuntimeError("Refuse downgrade: notice recipient history would be lost")
    op.drop_table("notice_deliveries")
    op.drop_column("articles", "recipient_ids")
