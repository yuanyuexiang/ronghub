"""Preserve source device profiles and extend prototype station classifications."""

import sqlalchemy as sa

from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_station_usage_type", "stations", type_="check")
    op.create_check_constraint(
        "ck_station_usage_type",
        "stations",
        "usage_type IN ('unknown','public','personal','bus','sanitation','logistics','taxi','other','car_sharing','community_shared','workplace','private_shared')",
    )
    op.drop_constraint("ck_station_construction_site", "stations", type_="check")
    op.create_check_constraint(
        "ck_station_construction_site",
        "stations",
        "construction_site IN ('unknown','public_institution','residential','enterprise','office','industrial','transit','culture_sports','highway','green_space','roadside','building_parking','other','scenic_area','bus_depot','fuel_station','taxi_site')",
    )
    op.add_column("equipment", sa.Column("equipment_type", sa.String(20), nullable=True))
    op.add_column("equipment", sa.Column("model_name", sa.String(100), nullable=True))
    op.add_column("equipment", sa.Column("source_unique_code", sa.String(100), nullable=True))
    op.add_column("equipment", sa.Column("manufactured_on", sa.Date(), nullable=True))
    op.add_column("connectors", sa.Column("name", sa.String(150), nullable=True))
    op.add_column("connectors", sa.Column("interface_type", sa.String(100), nullable=True))
    op.create_check_constraint(
        "ck_equipment_equipment_type",
        "equipment",
        "equipment_type IS NULL OR equipment_type IN ('dc','ac','ac_dc','wireless','ordered','v2g','other')",
    )
    op.create_check_constraint(
        "ck_equipment_manufactured_on",
        "equipment",
        "manufactured_on IS NULL OR manufactured_on BETWEEN DATE '1900-01-01' AND DATE '9998-12-31'",
    )
    op.create_index(
        "ix_equipment_station_type", "equipment", ["station_id", "equipment_type", "id"]
    )
    op.create_index("ix_connector_equipment_state", "connectors", ["equipment_id", "status", "id"])


def downgrade():
    bind = op.get_bind()
    populated = bind.scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM equipment WHERE equipment_type IS NOT NULL OR model_name IS NOT NULL OR source_unique_code IS NOT NULL OR manufactured_on IS NOT NULL) OR EXISTS(SELECT 1 FROM connectors WHERE name IS NOT NULL OR interface_type IS NOT NULL) OR EXISTS(SELECT 1 FROM stations WHERE usage_type IN ('car_sharing','community_shared','workplace','private_shared') OR construction_site IN ('scenic_area','bus_depot','fuel_station','taxi_site'))"
        )
    )
    if populated:
        raise RuntimeError(
            "Refuse downgrade: source device profiles or extended classifications would be lost"
        )
    op.drop_index("ix_connector_equipment_state", table_name="connectors")
    op.drop_index("ix_equipment_station_type", table_name="equipment")
    op.drop_constraint("ck_equipment_manufactured_on", "equipment", type_="check")
    op.drop_constraint("ck_equipment_equipment_type", "equipment", type_="check")
    op.drop_column("connectors", "interface_type")
    op.drop_column("connectors", "name")
    op.drop_column("equipment", "manufactured_on")
    op.drop_column("equipment", "source_unique_code")
    op.drop_column("equipment", "model_name")
    op.drop_column("equipment", "equipment_type")
    op.drop_constraint("ck_station_usage_type", "stations", type_="check")
    op.create_check_constraint(
        "ck_station_usage_type",
        "stations",
        "usage_type IN ('unknown','public','personal','bus','sanitation','logistics','taxi','other')",
    )
    op.drop_constraint("ck_station_construction_site", "stations", type_="check")
    op.create_check_constraint(
        "ck_station_construction_site",
        "stations",
        "construction_site IN ('unknown','public_institution','residential','enterprise','office','industrial','transit','culture_sports','highway','green_space','roadside','building_parking','other')",
    )
