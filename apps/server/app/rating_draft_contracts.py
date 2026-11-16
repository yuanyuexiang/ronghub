"""Bounded editable draft shapes; drafts are never executable scoring standards."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    field_validator,
    model_validator,
)

Text64 = Annotated[StrictStr, Field(max_length=64)]
Id = Annotated[int, Field(strict=True, ge=1, le=2147483647)]


class DraftModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*")
    @classmethod
    def no_nul(cls, value):
        if isinstance(value, str) and "\x00" in value:
            raise ValueError("草稿文字不能包含NUL")
        return value


class DraftBand(DraftModel):
    lower: Text64 = ""
    upper: Text64 | None = None
    score: Text64 = ""
    label: Annotated[StrictStr, Field(max_length=100)] = ""


class DraftIndicator(DraftModel):
    enabled: StrictBool | None = None
    code: Annotated[StrictStr, Field(max_length=50)] = ""
    name: Annotated[StrictStr, Field(max_length=100)] = ""
    weight: Text64 = ""
    max_score: Text64 = ""
    category: Annotated[StrictStr, Field(max_length=50)] = ""
    source: Literal["manual", "runtime"] = "manual"
    field_name: Literal[
        "", "expert_score", "station_power_kw", "equipment_count", "connector_count"
    ] = ""
    description: Annotated[StrictStr, Field(max_length=2000)] = ""
    effective_from: Annotated[StrictStr, Field(max_length=10)] = ""
    bands: list[DraftBand] = Field(default_factory=list, max_length=20)


class DraftCategory(DraftModel):
    code: Annotated[StrictStr, Field(max_length=50)] = ""
    name: Annotated[StrictStr, Field(max_length=100)] = ""
    weight: Text64 = ""


class DraftThreshold(DraftModel):
    minimum: Text64 = ""
    stars: Annotated[StrictStr, Field(max_length=10)] = ""


class RatingDraftData(DraftModel):
    code: Annotated[StrictStr, Field(max_length=60)] = ""
    name: Annotated[StrictStr, Field(max_length=150)] = ""
    version: Annotated[StrictStr, Field(max_length=10)] = ""
    categories: list[DraftCategory] = Field(default_factory=list, max_length=20)
    indicators: list[DraftIndicator] = Field(default_factory=list, max_length=50)
    thresholds: list[DraftThreshold] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def bounded(self):
        if len(self.model_dump_json().encode()) > 256 * 1024:
            raise ValueError("标准草稿不能超过256KiB")
        return self


class RatingDraftCreate(DraftModel):
    data: RatingDraftData


class RatingDraftUpdate(RatingDraftCreate):
    version: Id


class RatingDraftVersion(DraftModel):
    version: Id


class RatingDraftSummary(BaseModel):
    id: int
    version: int
    status: Literal["draft", "published", "deleted"]
    code: str
    name: str
    published_standard_id: int | None
    created_by: int
    updated_by: int
    created_at: str
    updated_at: str


class RatingDraftDetail(RatingDraftSummary):
    data: RatingDraftData


class RatingDraftPage(BaseModel):
    items: list[RatingDraftSummary]
    total: int
    page: int
    page_size: int
