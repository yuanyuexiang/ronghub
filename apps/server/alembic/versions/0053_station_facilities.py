"""Store source-reported station facilities, keeping historical unknowns nullable."""

import sqlalchemy as sa

from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("stations", sa.Column("supply_type", sa.String(20), nullable=True))
    op.add_column("stations", sa.Column("electricity_tariff", sa.String(30), nullable=True))
    op.add_column("stations", sa.Column("supply_capacity", sa.Numeric(14, 3), nullable=True))
    op.add_column("stations", sa.Column("supply_capacity_unit", sa.String(12), nullable=True))
    op.add_column("stations", sa.Column("independent_connection", sa.Boolean(), nullable=True))
    op.add_column("stations", sa.Column("parking_spaces", sa.Integer(), nullable=True))
    op.add_column("stations", sa.Column("commissioned_on", sa.Date(), nullable=True))
    op.add_column("stations", sa.Column("parking_fee_type", sa.String(20), nullable=True))
    op.add_column("stations", sa.Column("parking_fee_description", sa.String(500), nullable=True))
    op.add_column("stations", sa.Column("business_hours", sa.String(255), nullable=True))
    op.add_column("stations", sa.Column("filing_code", sa.String(100), nullable=True))
    op.add_column("stations", sa.Column("station_contact_name", sa.String(100), nullable=True))
    op.add_column("stations", sa.Column("station_contact_phone", sa.String(80), nullable=True))
    op.create_check_constraint(
        "ck_station_facility_supply_type",
        "stations",
        "supply_type IS NULL OR supply_type IN ('direct','transferred')",
    )
    op.create_check_constraint(
        "ck_station_facility_electricity_tariff",
        "stations",
        "electricity_tariff IS NULL OR electricity_tariff IN ('commercial','general_industrial','large_industrial','other')",
    )
    op.create_check_constraint(
        "ck_station_facility_capacity",
        "stations",
        "(supply_capacity IS NULL AND supply_capacity_unit IS NULL) OR (supply_capacity IS NOT NULL AND supply_capacity >= 0 AND supply_capacity <= 99999999999.999 AND supply_capacity_unit IS NOT NULL AND supply_capacity_unit IN ('unknown','kW','kVA'))",
    )
    op.create_check_constraint(
        "ck_station_facility_parking_spaces",
        "stations",
        "parking_spaces IS NULL OR parking_spaces >= 0",
    )
    op.create_check_constraint(
        "ck_station_facility_commissioned_on",
        "stations",
        "commissioned_on IS NULL OR commissioned_on BETWEEN DATE '1900-01-01' AND DATE '9998-12-31'",
    )
    op.create_check_constraint(
        "ck_station_facility_parking_fee_type",
        "stations",
        "parking_fee_type IS NULL OR parking_fee_type IN ('free','paid','limited_free','charging_discount','onsite')",
    )


def downgrade():
    columns = (
        "supply_type",
        "electricity_tariff",
        "supply_capacity",
        "supply_capacity_unit",
        "independent_connection",
        "parking_spaces",
        "commissioned_on",
        "parking_fee_type",
        "parking_fee_description",
        "business_hours",
        "filing_code",
        "station_contact_name",
        "station_contact_phone",
    )
    populated = op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM stations WHERE "
            + " OR ".join(name + " IS NOT NULL" for name in columns)
        )
    )
    if populated:
        raise RuntimeError("Refuse downgrade: station source facility data would be lost")
    op.drop_constraint("ck_station_facility_parking_fee_type", "stations", type_="check")
    op.drop_constraint("ck_station_facility_commissioned_on", "stations", type_="check")
    op.drop_constraint("ck_station_facility_parking_spaces", "stations", type_="check")
    op.drop_constraint("ck_station_facility_capacity", "stations", type_="check")
    op.drop_constraint("ck_station_facility_electricity_tariff", "stations", type_="check")
    op.drop_constraint("ck_station_facility_supply_type", "stations", type_="check")
    op.drop_column("stations", "station_contact_phone")
    op.drop_column("stations", "station_contact_name")
    op.drop_column("stations", "filing_code")
    op.drop_column("stations", "business_hours")
    op.drop_column("stations", "parking_fee_description")
    op.drop_column("stations", "parking_fee_type")
    op.drop_column("stations", "commissioned_on")
    op.drop_column("stations", "parking_spaces")
    op.drop_column("stations", "independent_connection")
    op.drop_column("stations", "supply_capacity_unit")
    op.drop_column("stations", "supply_capacity")
    op.drop_column("stations", "electricity_tariff")
    op.drop_column("stations", "supply_type")
