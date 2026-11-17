"""Keep application submission source/content/material evidence without rewriting history."""

import sqlalchemy as sa

from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "attachments", sa.Column("revision", sa.Integer(), nullable=False, server_default="1")
    )
    op.create_check_constraint("ck_attachment_revision", "attachments", "revision >= 1")
    op.add_column("attachments", sa.Column("category", sa.String(40), nullable=True))
    op.add_column(
        "attachments", sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.create_table(
        "application_submissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "application_id",
            sa.Integer(),
            sa.ForeignKey("station_applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("submitted_version", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("actor_username", sa.String(100), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_reviewed", sa.Boolean(), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "application_id", "submitted_version", name="uq_application_submission_version"
        ),
    )
    op.create_index(
        "ix_application_submissions_application_id", "application_submissions", ["application_id"]
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM application_submissions) OR EXISTS(SELECT 1 FROM attachments WHERE category IS NOT NULL OR NOT active OR revision > 1)"
        )
    ):
        raise RuntimeError(
            "Refuse downgrade: application submission or categorized/archived material evidence would be lost"
        )
    op.drop_table("application_submissions")
    op.drop_column("attachments", "active")
    op.drop_column("attachments", "category")
    op.drop_constraint("ck_attachment_revision", "attachments", type_="check")
    op.drop_column("attachments", "revision")
