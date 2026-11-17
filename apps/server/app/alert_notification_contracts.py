"""Private alert notification management contracts; recipients are not login identities."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

Category = Literal[
    "station_metric",
    "device_charging",
    "device_offline",
    "device_fault",
    "energy_power",
    "unconnected_device",
]
CATEGORIES = {
    "station_metric": "场站指标告警",
    "device_charging": "设备充电告警",
    "device_offline": "设备离线告警",
    "device_fault": "设备故障告警",
    "energy_power": "电量功率异常",
    "unconnected_device": "未接入设备告警",
}


class PreferenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(strict=True, ge=0, le=2147483647)
    in_app_enabled: StrictBool
    sms_enabled: StrictBool


class PreferenceResponse(BaseModel):
    category: Category
    label: str
    version: int
    in_app_enabled: bool | None
    sms_enabled: bool
    updated_by: int | None
    updated_at: str | None
    legacy_behavior: bool


class PreferenceList(BaseModel):
    items: list[PreferenceResponse]
    sms_mode: Literal["disabled", "demo"]
    notice: str


class RecipientInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)
    name: str = Field(min_length=1, max_length=80)
    phone: str = Field(pattern=r"^1[3-9][0-9]{9}$", repr=False)
    categories: list[Category] = Field(min_length=1, max_length=6)
    enabled: StrictBool = True

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        if "\x00" in value:
            raise ValueError("姓名不能包含空字符")
        return value

    @field_validator("categories")
    @classmethod
    def unique(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("接收类型不能重复")
        return sorted(value)


class RecipientUpdate(RecipientInput):
    enabled: StrictBool
    version: int = Field(strict=True, ge=1, le=2147483647)


class RecipientResponse(BaseModel):
    id: int
    name: str
    phone: str
    masked_phone: str
    categories: list[Category]
    enabled: bool
    deleted: bool
    version: int
    created_at: str
    updated_at: str


class RecipientPage(BaseModel):
    items: list[RecipientResponse]
    total: int
    page: int
    page_size: int


class DeliveryResponse(BaseModel):
    id: int
    routing_id: int
    event_id: int
    phase: str
    category: Category
    recipient_id: int
    recipient_version: int
    recipient_name: str
    masked_phone: str
    status: Literal["simulated", "provider_disabled"]
    created_at: str


class RoutingResponse(BaseModel):
    id: int
    event_id: int
    phase: str
    category: Category | None
    preference_version: int | None
    in_app_enabled: bool
    sms_enabled: bool
    sms_status: Literal["not_requested", "no_recipients", "simulated", "provider_disabled"]
    notice_id: int | None
    created_at: str


class RoutingPage(BaseModel):
    items: list[RoutingResponse]
    total: int
    page: int
    page_size: int


class DeliveryPage(BaseModel):
    items: list[DeliveryResponse]
    total: int
    page: int
    page_size: int
