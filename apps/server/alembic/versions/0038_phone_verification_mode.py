"""Keep simulation proofs distinct from real phone verification."""

import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade():
    # 0037 supports only the explicit demo adapter; no real transport existed.
    op.add_column(
        "verified_phones",
        sa.Column("verification_mode", sa.String(20), nullable=False, server_default="demo"),
    )
    op.alter_column("verified_phones", "verification_mode", server_default=None)


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM verified_phones")):
        raise RuntimeError("Refuse downgrade: phone proof modes must not be discarded")
    op.drop_column("verified_phones", "verification_mode")
