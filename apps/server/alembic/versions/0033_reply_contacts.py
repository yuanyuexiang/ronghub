"""Explicit immutable reply-time contacts, with unknown legacy values preserved."""

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("article_replies", sa.Column("contact_name", sa.String(100), nullable=True))
    op.add_column("article_replies", sa.Column("contact_phone", sa.String(80), nullable=True))


def downgrade():
    if op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM article_replies WHERE contact_name IS NOT NULL OR contact_phone IS NOT NULL"
        )
    ):
        raise RuntimeError("Refuse downgrade: historical reply contacts would be lost")
    op.drop_column("article_replies", "contact_phone")
    op.drop_column("article_replies", "contact_name")
