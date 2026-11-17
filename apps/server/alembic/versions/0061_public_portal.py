"""Explicit portal opt-in; every existing article remains private to authenticated readers."""

import sqlalchemy as sa

from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "articles",
        sa.Column("portal_public", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_check_constraint(
        "ck_article_portal_scope",
        "articles",
        "NOT portal_public OR (kind IN ('announcement','news','contact') AND audience = 'all' AND operator_id IS NULL AND recipient_ids IS NULL)",
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM articles WHERE portal_public)")):
        raise RuntimeError(
            "Refuse downgrade: explicit public portal publication decisions must be preserved"
        )
    op.drop_constraint("ck_article_portal_scope", "articles", type_="check")
    op.drop_column("articles", "portal_public")
