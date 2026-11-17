"""Recognize customer protocol publication; refuse downgrades with unsupported content."""

import sqlalchemy as sa

from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade():
    # Existing publication/evidence tables are reused, no data or official text is seeded.
    pass


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM articles WHERE kind='protocol')")
    ):
        raise RuntimeError(
            "Refuse downgrade: protocol publications and frozen evidence must be preserved"
        )
