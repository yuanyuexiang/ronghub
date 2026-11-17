"""Scoped map, energy-report and publication query responses."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.decimal_contracts import DecimalText

Count = Annotated[int, Field(ge=0)]


class MapStation(BaseModel):
    id: int
    name: str
    district: str
    operator_id: int
    operator_name: str
    address: str
    status: str
    station_type: str
    usage_type: str
    construction_site: str
    site_state: str
    source_sync_state: str
    source_first_connected_at: str | None
    power_kw: DecimalText
    connectors: Count
    observed_connectors: Count
    missing_connector_snapshots: Count
    runtime_state: str
    effective_stars: int | None
    longitude: DecimalText | None
    latitude: DecimalText | None
    coordinate_system: str
    location_is_demo: bool
    unplottable_reason: str | None
    synced_at: str


class MapStationPoint(MapStation):
    kind: Literal["station"]


class MapClusterPoint(BaseModel):
    kind: Literal["cluster"]
    key: str
    count: Count
    longitude: DecimalText
    latitude: DecimalText
    bounds: tuple[float, float, float, float]


class MapResponse(BaseModel):
    items: list[Annotated[MapStationPoint | MapClusterPoint, Field(discriminator="kind")]]
    matching: Count
    mappable: Count
    unplottable: Count
    in_view: Count
    cell_count: Count
    truncated: bool
    zoom: int
    coordinate_system: Literal["wgs84"]
    generated_at: str
    aggregation: str
    runtime_basis: str


class MapStationPage(BaseModel):
    items: list[MapStation]
    total: Count
    page: int
    page_size: int


class MapClassifications(BaseModel):
    usage_type: dict[str, str]
    construction_site: dict[str, str]
    site_state: dict[str, str]
    source_sync_state: dict[str, str]


class MapOptions(BaseModel):
    classifications: MapClassifications


class EnergyWindow(BaseModel):
    start_date: str
    end_date: str
    kwh: DecimalText | None
    observed_days: Count
    requested_days: Count
    station_days: Count
    expected_station_days: Count
    records: Count
    duplicate_records: Count
    complete_daily_observations: bool
    source_count: Count
    ended: bool


class EnergyComparison(BaseModel):
    period: EnergyWindow
    delta_kwh: DecimalText | None
    observed_change_percent: DecimalText | None
    unavailable_reason: str | None
    source_completeness_verified: Literal[False]
    basis: str


class EnergyComparisons(BaseModel):
    year_over_year: EnergyComparison
    period_over_period: EnergyComparison


class EnergyPeriod(EnergyWindow):
    period: str
    comparisons: EnergyComparisons


class EnergyDay(BaseModel):
    day: str
    kwh: DecimalText | None
    records: Count
    stations: Count


class EnergyStationRanking(BaseModel):
    id: int
    name: str
    district: str
    operator: str
    operator_id: int
    status: str
    connectors: Count
    power_kw: DecimalText
    kwh: DecimalText | None


class EnergyDimensionRanking(BaseModel):
    key: str
    name: str
    station_count: Count
    connectors: Count
    power_kw: DecimalText
    kwh: DecimalText | None


class EnergyUnavailable(BaseModel):
    utilization: str
    order_count: str
    charging_hours: str
    online_rate: str


class EnergyStatistics(BaseModel):
    generated_at: str
    start_date: str
    end_date: str
    timezone: Literal["Asia/Shanghai"]
    energy_date_basis: str
    granularity: Literal["day", "month", "year"]
    dimension: Literal["station", "district", "operator"]
    comparison_date_basis: str
    station_count: Count
    connectors: Count
    power_kw: DecimalText
    total_kwh: DecimalText | None
    observed_days: Count
    requested_days: Count
    coverage: EnergyWindow
    comparisons: EnergyComparisons
    previous_period: EnergyWindow
    timeline: list[EnergyPeriod]
    daily: list[EnergyDay]
    ranking: list[EnergyStationRanking]
    dimension_ranking: list[EnergyDimensionRanking]
    status_distribution: dict[str, Count]
    unavailable: EnergyUnavailable
    basis: str


class RatingPublication(BaseModel):
    id: int
    station_id: int
    station_name: str
    operator_name: str
    district: str
    address: str
    kind: str
    is_demo: bool
    valid_until: str | None
    published_at: str | None
    result_state: str
    effective_stars: int | None
    show_association_mark: bool


class RatingPublicationPage(BaseModel):
    items: list[RatingPublication]
    total: Count
    page: int
    page_size: int
    scope: str
    timezone: Literal["Asia/Shanghai"]
