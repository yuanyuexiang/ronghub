"""Human-reviewed regulator/association account requests, never public role grants."""

import sqlalchemy as sa
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "staff_applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("requested_role", sa.String(30), nullable=False),
        sa.Column("applicant_name", sa.String(100), nullable=False),
        sa.Column("contact_phone", sa.String(11), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_comment", sa.String(1000), nullable=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("username", sa.String(100), nullable=True),
        sa.Column("grant_snapshot", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_staff_applications_status", "staff_applications", ["status"])
    op.create_index(
        "uq_staff_pending_contact",
        "staff_applications",
        ["requested_role", "contact_phone"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "staff_application_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "application_id",
            sa.Integer(),
            sa.ForeignKey("staff_applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_staff_application_actions_application_id",
        "staff_application_actions",
        ["application_id"],
    )
    op.create_table(
        "staff_applicant_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "application_id",
            sa.Integer(),
            sa.ForeignKey("staff_applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_staff_applicant_sessions_application_id", "staff_applicant_sessions", ["application_id"]
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM staff_applications")):
        raise RuntimeError("Refuse downgrade: staff request and approval history would be lost")
    op.drop_table("staff_applicant_sessions")
    op.drop_table("staff_application_actions")
    op.drop_table("staff_applications")
