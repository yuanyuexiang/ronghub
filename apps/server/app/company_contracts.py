"""Company wire shapes; public summaries never contain private profile fields."""

from pydantic import BaseModel, Field, JsonValue, StrictBool, StrictInt, StrictStr


class CompanyContact(BaseModel):
    name: StrictStr
    phone: StrictStr
    email: StrictStr = ""
    position: StrictStr = ""
    primary: StrictBool = False


class CompanySummary(BaseModel):
    id: StrictInt
    name: StrictStr
    credit_code: StrictStr | None
    enabled: StrictBool
    version: int = Field(ge=1)


class CompanyList(BaseModel):
    items: list[CompanySummary]
    total: int = Field(ge=0)


class CompanyProfile(CompanySummary):
    registration_address: StrictStr
    legal_representative: StrictStr
    service_phone: StrictStr
    contacts: list[CompanyContact]


class CompanyHistory(BaseModel):
    action: StrictStr
    detail: dict[str, JsonValue]
    created_at: StrictStr


class CompanyDetail(CompanyProfile):
    history: list[CompanyHistory]
