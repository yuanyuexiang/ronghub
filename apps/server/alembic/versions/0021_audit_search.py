"""Index audit search ordering."""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_audit_time_id", "audit_logs", ["created_at", "id"])


def downgrade():
    op.drop_index("ix_audit_time_id", table_name="audit_logs")
