"""Independent hazard supervision with explicit progress and immutable feedback."""

import sqlalchemy as sa

from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "hazard_cases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("station_id", sa.Integer(), sa.ForeignKey("stations.id"), nullable=False),
        sa.Column("operator_id", sa.Integer(), sa.ForeignKey("operators.id"), nullable=False),
        sa.Column("station_name", sa.String(150), nullable=False),
        sa.Column("operator_name", sa.String(150), nullable=False),
        sa.Column("title", sa.String(150), nullable=False),
        sa.Column("description", sa.String(4000), nullable=False),
        sa.Column("rich_document", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("progress", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.CheckConstraint("severity IN ('normal','major')", name="ck_hazard_severity"),
        sa.CheckConstraint("status IN ('tracking','closed')", name="ck_hazard_tracking"),
        sa.CheckConstraint(
            "progress IN ('not_started','in_progress','completed')", name="ck_hazard_progress"
        ),
        sa.CheckConstraint(
            "(status = 'closed') = (closed_at IS NOT NULL)", name="ck_hazard_closure"
        ),
        sa.CheckConstraint("version >= 1", name="ck_hazard_version"),
    )
    op.create_table(
        "hazard_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("hazard_cases.id"), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("comment", sa.String(4000), nullable=False),
        sa.Column("rich_document", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("contact_name", sa.String(100), nullable=True),
        sa.Column("contact_phone", sa.String(80), nullable=True),
        sa.Column("performed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('publish','start','reply','close')", name="ck_hazard_action"
        ),
        sa.CheckConstraint(
            "(contact_name IS NULL) = (contact_phone IS NULL)", name="ck_hazard_contact_pair"
        ),
    )
    op.create_table(
        "hazard_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("hazard_cases.id"), nullable=False),
        sa.Column("action_id", sa.Integer(), sa.ForeignKey("hazard_actions.id"), nullable=True),
        sa.Column("uploaded_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("storage_key", sa.String(64), nullable=False, unique=True),
        sa.Column("filename", sa.String(180), nullable=False),
        sa.Column("content_type", sa.String(80), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for table, fields in {
        "hazard_cases": ["station_id", "operator_id"],
        "hazard_actions": ["case_id"],
        "hazard_attachments": ["case_id", "action_id"],
    }.items():
        for field in fields:
            op.create_index(f"ix_{table}_{field}", table, [field])


def downgrade():
    connection = op.get_bind()
    for table in ["hazard_attachments", "hazard_actions", "hazard_cases"]:
        if connection.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")):
            raise RuntimeError("Refuse downgrade: hazard supervision data must be preserved")
    for table in ["hazard_attachments", "hazard_actions", "hazard_cases"]:
        op.drop_table(table)
