"""Retain structured requirements and operator-reported execution facts without backfill."""

import sqlalchemy as sa

from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "inspection_tasks", sa.Column("rich_document", sa.JSON(none_as_null=True), nullable=True)
    )
    op.add_column(
        "inspection_actions", sa.Column("rich_document", sa.JSON(none_as_null=True), nullable=True)
    )
    op.add_column("inspection_actions", sa.Column("contact_name", sa.String(100), nullable=True))
    op.add_column("inspection_actions", sa.Column("contact_phone", sa.String(80), nullable=True))
    op.add_column(
        "inspection_actions", sa.Column("performed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "ck_inspection_contact_pair",
        "inspection_actions",
        "(contact_name IS NULL) = (contact_phone IS NULL)",
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM inspection_tasks WHERE rich_document IS NOT NULL) OR EXISTS(SELECT 1 FROM inspection_actions WHERE rich_document IS NOT NULL OR contact_name IS NOT NULL OR contact_phone IS NOT NULL OR performed_at IS NOT NULL)"
        )
    ):
        raise RuntimeError(
            "Refuse downgrade: structured inspection requirements or reported execution facts would be lost"
        )
    op.drop_constraint("ck_inspection_contact_pair", "inspection_actions", type_="check")
    for field in ["performed_at", "contact_phone", "contact_name", "rich_document"]:
        op.drop_column("inspection_actions", field)
    op.drop_column("inspection_tasks", "rich_document")
