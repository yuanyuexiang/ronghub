"""Explicit alert configuration and reference-preserving lifecycle metadata."""

import sqlalchemy as sa

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "alert_rules", sa.Column("configuration", sa.JSON(none_as_null=True), nullable=True)
    )
    op.add_column(
        "alert_rules", sa.Column("revision", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column(
        "alert_rules",
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    for name in ["created_by", "updated_by"]:
        op.add_column("alert_rules", sa.Column(name, sa.Integer(), nullable=True))
        op.create_foreign_key(f"fk_alert_rule_{name}", "alert_rules", "users", [name], ["id"])
    for name in ["created_at", "updated_at"]:
        op.add_column("alert_rules", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint("ck_alert_rule_revision", "alert_rules", "revision >= 1")


def downgrade():
    if op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM alert_rules WHERE configuration IS NOT NULL OR revision<>1 OR archived OR created_by IS NOT NULL OR updated_by IS NOT NULL OR created_at IS NOT NULL OR updated_at IS NOT NULL"
        )
    ):
        raise RuntimeError(
            "Refuse downgrade: configured alert rules or lifecycle evidence would be lost"
        )
    op.drop_constraint("ck_alert_rule_revision", "alert_rules", type_="check")
    for name in ["created_by", "updated_by"]:
        op.drop_constraint(f"fk_alert_rule_{name}", "alert_rules", type_="foreignkey")
    for name in [
        "configuration",
        "revision",
        "archived",
        "created_by",
        "updated_by",
        "created_at",
        "updated_at",
    ]:
        op.drop_column("alert_rules", name)
