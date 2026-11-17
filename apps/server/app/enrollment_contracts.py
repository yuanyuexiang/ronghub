"""Applicant and reviewer responses; credentials and private storage keys never appear."""

from typing import Literal

from pydantic import BaseModel, Field

from app.company_contracts import CompanyContact
from app.material_contracts import ApplicationMaterialItem


class EnrollmentRecord(BaseModel):
    id: int
    username: str
    company_name: str
    credit_code: str
    registration_address: str
    contacts: list[CompanyContact]
    status: Literal["draft", "submitted", "rejected", "approved"]
    version: int = Field(ge=1)
    operator_id: int | None
    user_id: int | None
    created_at: str
    is_demo: bool


class EnrollmentSessionResponse(EnrollmentRecord):
    csrf_token: str


class EnrollmentHistory(BaseModel):
    action: str
    comment: str
    created_at: str


class EnrollmentProgress(EnrollmentSessionResponse):
    history: list[EnrollmentHistory]


class RegistrationDetail(EnrollmentRecord):
    history: list[EnrollmentHistory]
    attachments: list[ApplicationMaterialItem]


class RegistrationPage(BaseModel):
    items: list[EnrollmentRecord]
    total: int = Field(ge=0)


class RegistrationRoleOption(BaseModel):
    id: int
    name: str
    version: int = Field(ge=1)


class RegistrationOptions(BaseModel):
    items: list[RegistrationRoleOption]


class EnrollmentMaterial(ApplicationMaterialItem):
    submitted_version: int | None


class EnrollmentMaterials(BaseModel):
    items: list[EnrollmentMaterial]
