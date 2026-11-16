"""Private subsidy evidence and truthful legacy notes."""

import sqlalchemy as sa

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("subsidy_claims", sa.Column("application_note", sa.String(2000), nullable=True))
    op.create_table(
        "subsidy_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("claim_id", sa.Integer(), sa.ForeignKey("subsidy_claims.id"), nullable=False),
        sa.Column("uploaded_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("submitted_version", sa.Integer(), nullable=True),
        sa.Column("storage_key", sa.String(64), nullable=False, unique=True),
        sa.Column("filename", sa.String(180), nullable=False),
        sa.Column("content_type", sa.String(80), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_subsidy_attachments_claim_id", "subsidy_attachments", ["claim_id"])
    for name in ["login_sessions", "enrollment_sessions", "staff_applicant_sessions"]:
        op.execute(sa.text(f"DELETE FROM {name}"))


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT count(*) FROM subsidy_attachments")) or connection.scalar(
        sa.text("SELECT count(*) FROM subsidy_claims WHERE application_note IS NOT NULL")
    ):
        raise RuntimeError("Refuse downgrade: subsidy notes or evidence would be lost")
    if connection.scalar(
        sa.text(
            "SELECT count(*) FROM role_profiles WHERE permissions::jsonb ?| array['subsidy.edit','subsidy.materials']"
        )
    ):
        raise RuntimeError("Refuse downgrade: explicitly review subsidy evidence grants")
    op.drop_index("ix_subsidy_attachments_claim_id", table_name="subsidy_attachments")
    op.drop_table("subsidy_attachments")
    op.drop_column("subsidy_claims", "application_note")
