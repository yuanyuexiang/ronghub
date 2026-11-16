"""Structured article documents and private frozen source attachments."""

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("articles", sa.Column("rich_document", sa.JSON(none_as_null=True), nullable=True))
    op.create_table(
        "article_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("article_id", sa.Integer(), sa.ForeignKey("articles.id"), nullable=False),
        sa.Column("uploaded_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("filename", sa.String(180), nullable=False),
        sa.Column("storage_key", sa.String(48), nullable=False, unique=True),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("frozen", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_article_attachments_article_id", "article_attachments", ["article_id"])


def downgrade():
    count = op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM articles WHERE rich_document IS NOT NULL AND json_typeof(rich_document) != 'null'"
        )
    )
    if count:
        raise RuntimeError("Refuse downgrade: structured article content would be lost")
    count = op.get_bind().scalar(sa.text("SELECT count(*) FROM article_attachments"))
    if count:
        raise RuntimeError("Refuse downgrade: article attachment metadata would be lost")
    op.drop_table("article_attachments")
    op.drop_column("articles", "rich_document")
