"""Source-declared historical service coverage; independent of financial measurement."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, localcontext
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from app.decimal_contracts import DecimalText

Seconds = Decimal
Kind = Literal["station_utilization", "station_fault", "station_offline"]
Period = Literal["natural_week", "natural_month"]
BEIJING = ZoneInfo("Asia/Shanghai")


class Member(BaseModel):
    model_config = ConfigDict(extra="forbid")
    equipment_external_id: str = Field(min_length=1, max_length=100)
    equipment_name: str | None = Field(default=None, max_length=150)
    expected_seconds: Decimal | None = Field(
        ge=0, max_digits=18, decimal_places=6, allow_inf_nan=False
    )
    observed_seconds: Decimal | None = Field(
        ge=0, max_digits=18, decimal_places=6, allow_inf_nan=False
    )
    unknown_seconds: Decimal | None = Field(
        ge=0, max_digits=18, decimal_places=6, allow_inf_nan=False
    )
    affected_seconds: Decimal | None = Field(
        ge=0, max_digits=18, decimal_places=6, allow_inf_nan=False
    )

    @model_validator(mode="after")
    def consistent(self):
        values = [
            self.expected_seconds,
            self.observed_seconds,
            self.unknown_seconds,
            self.affected_seconds,
        ]
        if any(value is None for value in values):
            if any(value is not None for value in values):
                raise ValueError("覆盖秒数须全部明确或全部未知")
        elif (
            self.observed_seconds > self.expected_seconds
            or self.unknown_seconds + self.affected_seconds > self.observed_seconds
        ):
            raise ValueError("覆盖、未知及影响秒数不一致")
        return self


class SourcePeriod(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_id: str = Field(min_length=1, max_length=100)
    metric: Kind
    period: Period
    period_start: date = Field(ge=date(1900, 1, 1), le=date(9998, 12, 31))
    period_end: date
    basis: Literal["source_declared_equipment_service_seconds_v1"]
    equipment_external_ids: list[str] = Field(max_length=1000)
    members_complete: StrictBool
    members: list[Member] = Field(max_length=1000)
    source_updated_at: datetime

    @field_validator("external_id")
    @classmethod
    def valid_id(cls, value):
        if "\x00" in value:
            raise ValueError("源标识不能含空字符")
        return value

    @model_validator(mode="after")
    def valid(self):
        start = self.period_start
        end = (
            start + timedelta(days=7)
            if self.period == "natural_week"
            else (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        )
        if self.period_end != end or (
            start.weekday() != 0 if self.period == "natural_week" else start.day != 1
        ):
            raise ValueError("须为完整自然周或自然月，结束不含")
        closed = datetime.combine(end, time.min, BEIJING)
        if self.source_updated_at.utcoffset() is None:
            raise ValueError("来源修订时间须带时区")
        try:
            stamp = self.source_updated_at.astimezone(UTC)
        except (OverflowError, ValueError) as error:
            raise ValueError("来源时间超出支持范围") from error
        if (
            closed > datetime.now(BEIJING)
            or stamp < closed
            or stamp > datetime.now(UTC) + timedelta(minutes=5)
        ):
            raise ValueError("来源事实须已闭合，修订不早于周期结束且不超前5分钟")
        self.source_updated_at = stamp
        keys = self.equipment_external_ids
        actual = [row.equipment_external_id for row in self.members]
        if (
            any(not value or len(value) > 100 or "\x00" in value for value in [*keys, *actual])
            or len(set(keys)) != len(keys)
            or len(set(actual)) != len(actual)
        ):
            raise ValueError("设备集合须为有界唯一标识")
        if not set(actual) <= set(keys) or (self.members_complete and set(actual) != set(keys)):
            raise ValueError("返回设备与声明历史集合不一致")
        seconds = Decimal((end - start).days * 86400)
        if any(
            row.expected_seconds is not None and row.expected_seconds > seconds
            for row in self.members
        ):
            raise ValueError("设备应覆盖秒数不能超过该自然周期")
        self.equipment_external_ids = sorted(keys)
        self.members = sorted(self.members, key=lambda member: member.equipment_external_id)
        return self


def aggregate(value: SourcePeriod):
    reason = None
    if not value.members_complete:
        reason = "incomplete_members"
    elif any(row.expected_seconds is None for row in value.members):
        reason = "unknown_service"
    elif any(row.observed_seconds != row.expected_seconds for row in value.members):
        reason = "partial_coverage"
    elif any(row.unknown_seconds != 0 for row in value.members):
        reason = "unknown_states"
    with localcontext() as context:
        context.prec = 50
        denominator = sum(
            (row.expected_seconds for row in value.members if row.expected_seconds is not None),
            Decimal(0),
        )
        numerator = sum(
            (row.affected_seconds for row in value.members if row.affected_seconds is not None),
            Decimal(0),
        )
        if reason is None and denominator == 0:
            reason = "no_service_denominator"
    return numerator, denominator, reason


class SourcePeriodProof(BaseModel):
    fact_id: int
    fact_version: int
    source: str
    source_record: SourcePeriod
    numerator_seconds: DecimalText | None
    denominator_seconds: DecimalText | None
    quality_reason: (
        Literal[
            "incomplete_members",
            "unknown_service",
            "partial_coverage",
            "unknown_states",
            "no_service_denominator",
        ]
        | None
    )


class PeriodFactResponse(BaseModel):
    id: int
    station_id: int
    station_name: str
    operator_id: int
    operator_name: str
    source: str
    version: int
    source_record: SourcePeriod
    percent: DecimalText | None
    numerator_seconds: DecimalText | None
    denominator_seconds: DecimalText | None
    quality_reason: str | None
    is_demo: bool
    verified: Literal[False]
    synced_at: str


class PeriodFactPage(BaseModel):
    items: list[PeriodFactResponse]
    total: int
    page: int
    page_size: int
    notice: str
