"""Short-lived browser-bound one-use password login challenges."""

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "captcha_challenges",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("binding_hash", sa.String(64), nullable=False),
        sa.Column("answer_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_captcha_challenges_binding_hash", "captcha_challenges", ["binding_hash"])
    op.create_index("ix_captcha_challenges_expires_at", "captcha_challenges", ["expires_at"])


def downgrade():
    op.drop_table("captcha_challenges")
