"""Private inspection plan drafts and atomic per-station publication, without legacy inference."""

import sqlalchemy as sa

from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inspection_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("request_key", sa.String(36), nullable=False),
        sa.Column("title", sa.String(150), nullable=False, server_default=""),
        sa.Column("description", sa.String(4000), nullable=False, server_default=""),
        sa.Column("rich_document", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("targets", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("created_by", "request_key", name="uq_inspection_plan_request"),
        sa.CheckConstraint("version >= 1", name="ck_inspection_plan_version"),
        sa.CheckConstraint(
            "status IN ('draft','published','deleted')", name="ck_inspection_plan_status"
        ),
        sa.CheckConstraint(
            "(status = 'published') = (published_at IS NOT NULL)",
            name="ck_inspection_plan_publication",
        ),
    )
    op.create_index("ix_inspection_plans_created_by", "inspection_plans", ["created_by"])
    op.add_column("inspection_tasks", sa.Column("plan_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_inspection_task_plan", "inspection_tasks", "inspection_plans", ["plan_id"], ["id"]
    )
    op.create_index("ix_inspection_tasks_plan_id", "inspection_tasks", ["plan_id"])
    op.create_unique_constraint(
        "uq_inspection_plan_station", "inspection_tasks", ["plan_id", "station_id"]
    )
    op.alter_column(
        "inspection_tasks", "due_at", existing_type=sa.DateTime(timezone=True), nullable=True
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM inspection_plans) OR EXISTS(SELECT 1 FROM inspection_tasks WHERE plan_id IS NOT NULL OR due_at IS NULL)"
        )
    ):
        raise RuntimeError(
            "Refuse downgrade: inspection plans or unknown task deadlines would be lost"
        )
    op.alter_column(
        "inspection_tasks", "due_at", existing_type=sa.DateTime(timezone=True), nullable=False
    )
    op.drop_constraint("uq_inspection_plan_station", "inspection_tasks", type_="unique")
    op.drop_index("ix_inspection_tasks_plan_id", table_name="inspection_tasks")
    op.drop_constraint("fk_inspection_task_plan", "inspection_tasks", type_="foreignkey")
    op.drop_column("inspection_tasks", "plan_id")
    op.drop_index("ix_inspection_plans_created_by", table_name="inspection_plans")
    op.drop_table("inspection_plans")
