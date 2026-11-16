"""Expand legacy ratings bundles without creating runtime wildcard grants."""

from alembic import op
import sqlalchemy as sa

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

# Frozen migration policy: regulator bundles preserve their delegation ceiling;
# enterprise bundles preserve only actions the enterprise role could perform.
GRANTS = {
    "regulator": [
        "ratings.create",
        "ratings.edit",
        "ratings.materials",
        "ratings.submit",
        "ratings.withdraw",
        "ratings.review",
        "ratings.publish",
        "ratings.delist",
        "ratings.standards",
    ],
    "operator": [
        "ratings.create",
        "ratings.edit",
        "ratings.materials",
        "ratings.submit",
        "ratings.withdraw",
    ],
    "association": ["ratings.review", "ratings.publish", "ratings.delist", "ratings.standards"],
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
        if "ratings.write" not in old:
            continue
        grants = sorted((old - {"ratings.write"}) | set(GRANTS.get(row["base_role"], [])))
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
