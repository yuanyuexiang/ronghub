"""Independent closed-period coverage, monitor start and durable evaluations."""

import sqlalchemy as sa

from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "alert_rules", sa.Column("monitor_from", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "alert_period_facts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("station_id", sa.Integer(), nullable=False, index=True),
        sa.Column("operator_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("external_id", sa.String(100), nullable=False),
        sa.Column("metric", sa.String(40), nullable=False),
        sa.Column("period", sa.String(30), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["station_id", "operator_id", "source"],
            ["stations.id", "stations.operator_id", "stations.source"],
            name="fk_alert_period_owner",
        ),
        sa.UniqueConstraint("source", "operator_id", "external_id", name="uq_alert_period_source"),
        sa.UniqueConstraint(
            "source",
            "station_id",
            "metric",
            "period",
            "period_start",
            name="uq_alert_period_natural",
        ),
        sa.CheckConstraint("version>=1", name="ck_alert_period_version"),
    )
    op.create_table(
        "alert_period_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fact_id", sa.Integer(), sa.ForeignKey("alert_period_facts.id"), nullable=False),
        sa.Column("fact_version", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), sa.ForeignKey("alert_rules.id"), nullable=False),
        sa.Column(
            "observation_id",
            sa.Integer(),
            sa.ForeignKey("configured_alert_observations.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "fact_id", "fact_version", "rule_id", name="uq_alert_period_evaluation"
        ),
    )


def downgrade():
    db = op.get_bind()
    if (
        db.scalar(sa.text("SELECT count(*) FROM alert_period_facts"))
        or db.scalar(sa.text("SELECT count(*) FROM alert_period_evaluations"))
        or db.scalar(sa.text("SELECT count(*) FROM alert_rules WHERE monitor_from IS NOT NULL"))
    ):
        raise RuntimeError(
            "Refuse downgrade: period evidence, evaluations or monitor start would be lost"
        )
    op.drop_table("alert_period_evaluations")
    op.drop_table("alert_period_facts")
    op.drop_column("alert_rules", "monitor_from")
