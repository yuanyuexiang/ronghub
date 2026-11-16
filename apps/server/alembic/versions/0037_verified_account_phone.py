"""Independent verified account phones and purpose-bound SMS challenges."""

import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "verified_phones",
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("phone", sa.String(11), nullable=False, unique=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
    )
    op.create_table(
        "sms_challenges",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("binding_hash", sa.String(64), nullable=False),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("phone", sa.String(11), nullable=True),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("credential_version", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_mode", sa.String(20), nullable=False),
        sa.Column("receipt_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sms_challenges_user_id", "sms_challenges", ["user_id"])
    op.create_index("ix_sms_challenges_expires_at", "sms_challenges", ["expires_at"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM verified_phones")):
        raise RuntimeError("Refuse downgrade: verified account phone proofs would be lost")
    op.drop_table("sms_challenges")
    op.drop_table("verified_phones")
