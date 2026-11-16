"""Explicit report grants and truthful per-action transition snapshots."""

import sqlalchemy as sa

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade():
    for name in ["subsidy_actions", "rating_actions"]:
        op.add_column(
            name, sa.Column("transition_snapshot", sa.JSON(none_as_null=True), nullable=True)
        )
    # No invented historical states and no implicit grants for custom profiles.
    for name in ["login_sessions", "enrollment_sessions", "staff_applicant_sessions"]:
        op.execute(sa.text(f"DELETE FROM {name}"))


def downgrade():
    for name in ["subsidy_actions", "rating_actions"]:
        if op.get_bind().scalar(
            sa.text(f"SELECT count(*) FROM {name} WHERE transition_snapshot IS NOT NULL")
        ):
            raise RuntimeError("Refuse downgrade: frozen business transitions would be lost")
    if op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM role_profiles WHERE permissions::jsonb ?| array['subsidy.export','ratings.export']"
        )
    ):
        raise RuntimeError("Refuse downgrade: review business export grants explicitly")
    for name in ["subsidy_actions", "rating_actions"]:
        op.drop_column(name, "transition_snapshot")
