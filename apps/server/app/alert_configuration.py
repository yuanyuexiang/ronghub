"""Explicit immutable multi-level alert configuration, independent of legacy thresholds."""

import itertools
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

Number = Annotated[Decimal, Field(max_digits=18, decimal_places=6, allow_inf_nan=False)]
Kind = Literal[
    "device_temperature",
    "device_current",
    "device_leakage",
    "station_utilization",
    "station_fault",
    "station_offline",
]
PERIOD_KINDS = {"station_utilization", "station_fault", "station_offline"}
UNITS = {
    "device_temperature": "℃",
    "device_current": "%额定电流",
    "device_leakage": "mA",
    "station_utilization": "%",
    "station_fault": "%",
    "station_offline": "%",
}
LEVELS = ["general", "severe", "critical"]


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    configuration_version: Literal[1]
    kind: Kind
    direction: Literal["gte", "lte"]
    period: Literal["instant", "natural_week", "natural_month"]
    thresholds: dict[Literal["general", "severe", "critical"], Number] = Field(
        min_length=1, max_length=3
    )
    recover_value: Number

    @field_validator("configuration_version", mode="before")
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int:
            raise ValueError("配置版本须为整数")
        return value

    @model_validator(mode="after")
    def valid(self):
        if (self.kind in PERIOD_KINDS) != (self.period != "instant"):
            raise ValueError("场站指标须选自然周/月；设备告警须为即时观测")
        values = [self.thresholds[key] for key in LEVELS if key in self.thresholds]
        if self.direction == "gte":
            if (
                any(a >= b for a, b in itertools.pairwise(values))
                or self.recover_value >= values[0]
            ):
                raise ValueError("高值告警须随级别严格递增，恢复阈值低于最低触发阈值")
        elif any(a <= b for a, b in itertools.pairwise(values)) or self.recover_value <= values[0]:
            raise ValueError("低值告警须随级别严格递减，恢复阈值高于最低触发阈值")
        bounds = (
            (-273.15, 1000000)
            if self.kind == "device_temperature"
            else (0, 100)
            if self.kind in PERIOD_KINDS
            else (0, 1000000)
        )
        if any(
            not Decimal(str(bounds[0])) <= value <= Decimal(str(bounds[1]))
            for value in [*values, self.recover_value]
        ):
            raise ValueError("阈值超出指标允许范围")
        return self

    def level(self, value: Decimal):
        return next(
            (
                key
                for key in reversed(LEVELS)
                if key in self.thresholds
                and (
                    value >= self.thresholds[key]
                    if self.direction == "gte"
                    else value <= self.thresholds[key]
                )
            ),
            None,
        )

    @property
    def trigger(self):
        return next(self.thresholds[key] for key in LEVELS if key in self.thresholds)


class RuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    code: str = Field(min_length=1, max_length=60)
    version: int = Field(strict=True, ge=1, le=2147483647)
    name: str = Field(min_length=1, max_length=150)
    configuration: Configuration
    notifications_enabled: StrictBool = True

    @field_validator("code", "name")
    @classmethod
    def valid_text(cls, value):
        if "\x00" in value:
            raise ValueError("不能包含空字符")
        return value


class RuleControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(strict=True, ge=1, le=2147483647)
    enabled: StrictBool


class RuleVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(strict=True, ge=1, le=2147483647)


class Preview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    configuration: Configuration
    value: Number | None
    # Current is measured in amperes; source must provide a positive rated reference.
    rated_current_a: Number | None = None
    period_start: date | None = Field(None, ge=date(1900, 1, 1), le=date(9998, 12, 31))
    period_end: date | None = None
    coverage_complete: StrictBool

    @model_validator(mode="after")
    def evidence(self):
        c = self.configuration
        if self.coverage_complete and self.value is None:
            raise ValueError("完整观测不能缺少测量值")
        if c.kind == "device_current":
            if (
                (self.rated_current_a is not None and self.rated_current_a <= 0)
                or (self.value is not None and self.value < 0)
                or (self.coverage_complete and self.rated_current_a is None)
            ):
                raise ValueError("过流比较须提供非负实测电流及正额定电流")
        elif self.rated_current_a is not None:
            raise ValueError("非电流指标不能传额定电流")
        if c.kind in PERIOD_KINDS:
            if (
                self.period_start is None
                or self.period_end is None
                or (self.value is not None and not 0 <= self.value <= 100)
            ):
                raise ValueError("周期指标须有完整周期起止及0至100的观测百分比")
            start, end = self.period_start, self.period_end
            expected = (
                start + timedelta(days=7)
                if c.period == "natural_week"
                else (start.replace(day=28) + timedelta(days=4)).replace(day=1)
            )
            if end != expected or (
                start.weekday() != 0 if c.period == "natural_week" else start.day != 1
            ):
                raise ValueError("周期须为完整自然周或自然月，结束日期不含")
            if datetime.combine(end, time.min, ZoneInfo("Asia/Shanghai")) > datetime.now(
                ZoneInfo("Asia/Shanghai")
            ):
                raise ValueError("只能判断已结束的北京时间自然周期")
        elif self.period_start is not None or self.period_end is not None:
            raise ValueError("即时设备观测不接受统计周期")
        if (
            c.kind == "device_temperature"
            and self.value is not None
            and self.value < Decimal("-273.15")
        ):
            raise ValueError("温度低于绝对零度")
        if c.kind == "device_leakage" and self.value is not None and self.value < 0:
            raise ValueError("漏电观测不能为负数")
        return self
