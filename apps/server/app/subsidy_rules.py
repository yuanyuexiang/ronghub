"""Versioned prototype rule configuration; it is not an execution-data adapter."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MoneyRate = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=6)]
Percentage = Annotated[Decimal, Field(ge=0, le=100, max_digits=7, decimal_places=4)]


class DeviceSubsidyRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    configuration_version: Literal[1]
    swap_rate: MoneyRate
    generation_period: Literal["day", "month", "year"]
    star_coefficients: dict[str, MoneyRate] = Field(min_length=6, max_length=6)
    fault_rate_below: Percentage
    offline_rate_below: Percentage

    @field_validator("configuration_version", mode="before")
    @classmethod
    def exact_version_type(cls, value):
        if type(value) is not int:
            raise ValueError("配置版本必须是整数")
        return value

    @model_validator(mode="after")
    def exact_stars(self):
        if set(self.star_coefficients) != {str(star) for star in range(6)}:
            raise ValueError("必须明确填写无星至五星的六档系数")
        return self


class DeviceExecutableRules(DeviceSubsidyRules):
    configuration_version: Literal[2]
    metric_basis: Literal["calendar_day_seconds_v1"]
    device_scope_basis: Literal["source_declared_station_day_v1"]
    rating_reference: Literal["period_end_publication_snapshot_v1"]
    rounding: Literal["HALF_UP_PER_DEVICE_DAY_THEN_COEFFICIENT_2DP"]
