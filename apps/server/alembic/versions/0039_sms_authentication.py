"""Anonymous purpose-isolated SMS authentication subjects and phone proof versions."""

import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("sms_challenges", sa.Column("subject_hash", sa.String(64), nullable=True))
    op.add_column("sms_challenges", sa.Column("phone_version", sa.Integer(), nullable=True))
    op.create_index("ix_sms_challenges_subject_hash", "sms_challenges", ["subject_hash"])


def downgrade():
    op.execute(sa.text("DELETE FROM sms_challenges"))
    op.drop_index("ix_sms_challenges_subject_hash", table_name="sms_challenges")
    op.drop_column("sms_challenges", "phone_version")
    op.drop_column("sms_challenges", "subject_hash")
