"""Preserve structured notice reply formatting, without rewriting historical plain text."""

import sqlalchemy as sa

from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "article_replies", sa.Column("rich_document", sa.JSON(none_as_null=True), nullable=True)
    )


def downgrade():
    count = op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM article_replies WHERE rich_document IS NOT NULL AND json_typeof(rich_document) != 'null'"
        )
    )
    if count:
        raise RuntimeError("Refuse downgrade: structured notice replies would be lost")
    op.drop_column("article_replies", "rich_document")
