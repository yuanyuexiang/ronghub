"""Version content soft deletion and independent export/deletion grants."""

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade():
    # No custom profile receives new capabilities implicitly. Clients reload grants.
    op.execute(sa.text("DELETE FROM login_sessions"))
    op.execute(sa.text("DELETE FROM enrollment_sessions"))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM articles WHERE status = 'deleted'")):
        raise RuntimeError("Refuse downgrade: deleted article lifecycle needs current application")
    if op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM role_profiles WHERE permissions::jsonb ?| array['content.delete','content.export']"
        )
    ):
        raise RuntimeError("Refuse downgrade: review independent content grants explicitly")
