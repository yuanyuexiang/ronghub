"""Explicit source coordinates: unknown systems are stored, never guessed or converted."""

from decimal import Decimal, InvalidOperation

SYSTEMS = {"wgs84", "gcj02", "bd09", "unknown"}
TYPES = {"charging", "swapping", "mixed", "unknown"}


def values(record):
    result = {}
    if "station_type" in record:
        if record["station_type"] not in TYPES:
            raise ValueError("Unknown station type")
        result["station_type"] = record["station_type"]
    keys = {"longitude", "latitude", "coordinate_system", "location_is_demo"}
    if not keys.intersection(record):
        return result  # Omitted optional location must not erase previously known data.
    if not {"longitude", "latitude", "coordinate_system"}.issubset(record):
        raise ValueError("Location requires an explicit coordinate pair and system")
    system = record["coordinate_system"]
    if system not in SYSTEMS:
        raise ValueError("Unknown coordinate system")
    lon, lat = record["longitude"], record["latitude"]
    if (lon is None) != (lat is None):
        raise ValueError("Coordinate pair is incomplete")
    if lon is not None:
        try:
            lon, lat = Decimal(str(lon)), Decimal(str(lat))
        except (InvalidOperation, ValueError) as error:
            raise ValueError("Invalid coordinate") from error
        if (
            not lon.is_finite()
            or not lat.is_finite()
            or not -180 <= lon <= 180
            or not -90 <= lat <= 90
        ):
            raise ValueError("Coordinate outside permitted range")
    demo = record.get("location_is_demo", False)
    if not isinstance(demo, bool):
        raise TypeError("Invalid demo-location flag")
    return {
        **result,
        "longitude": lon,
        "latitude": lat,
        "coordinate_system": system,
        "location_is_demo": demo,
    }
