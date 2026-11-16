"""Expand legacy content bundles without creating runtime wildcard grants."""

import sqlalchemy as sa

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

# Frozen migration policy: regulator bundles preserve their delegation ceiling;
# enterprise bundles preserve only actions the enterprise role could perform.
GRANTS = {
    "regulator": [
        "content.manage",
        "content.create",
        "content.edit",
        "content.publish",
        "content.withdraw",
    ],
}


def upgrade():
    db = op.get_bind()
    profiles = sa.table(
        "role_profiles",
        sa.column("id", sa.Integer),
        sa.column("base_role", sa.String),
        sa.column("permissions", sa.JSON),
        sa.column("version", sa.Integer),
    )
    for row in db.execute(sa.select(profiles)).mappings():
        old = set(row["permissions"])
        if "content.write" not in old:
            continue
        grants = sorted((old - {"content.write"}) | set(GRANTS.get(row["base_role"], [])))
        db.execute(
            profiles.update()
            .where(profiles.c.id == row["id"])
            .values(permissions=grants, version=profiles.c.version + 1)
        )
    # All clients must reload current permission names after this authorization change.
    db.execute(sa.text("DELETE FROM login_sessions"))
    db.execute(sa.text("DELETE FROM enrollment_sessions"))


def downgrade():
    raise RuntimeError(
        "Fine-grained grants cannot safely collapse into a broad write grant; restore the pre-migration backup or review grants explicitly"
    )
