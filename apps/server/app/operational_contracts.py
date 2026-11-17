"""Operational API shapes: public metadata excludes worker lease credentials."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.decimal_contracts import DecimalText

Count = Annotated[int, Field(strict=True, ge=0)]


class SyncAccepted(BaseModel):
    id: int
    status: str


class SyncTaskItem(SyncAccepted):
    request_key: str
    source: str
    operator_id: int
    attempts: Count
    error: str | None
    result: dict[str, Count] | None
    created_at: str


class SyncTaskList(BaseModel):
    items: list[SyncTaskItem]


class LegacyAlertRule(BaseModel):
    id: int
    code: str
    version: int
    name: str
    metric: str
    unit: str
    trigger_value: DecimalText
    recover_value: DecimalText
    notifications_enabled: bool
    is_demo: bool


class LegacyAlertRules(BaseModel):
    items: list[LegacyAlertRule]


class LegacyRuleCreated(BaseModel):
    id: int


class LegacySampleResult(BaseModel):
    outcome: Literal["ignored_stale", "opened", "merged", "recovered", "normal"]
    event_id: int | None
