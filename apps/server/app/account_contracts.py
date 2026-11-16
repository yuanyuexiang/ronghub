"""Authorization management responses contain grants, never authentication proofs."""

from typing import Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt, StrictStr

Role = Literal["regulator", "association", "operator"]


class RoleProfileResponse(BaseModel):
    id: StrictInt
    name: StrictStr
    base_role: Role
    permissions: list[StrictStr]
    version: int = Field(ge=1)


class PermissionCatalogEntry(BaseModel):
    code: StrictStr
    name: StrictStr


class RoleCatalogResponse(BaseModel):
    catalog: list[PermissionCatalogEntry]
    base_roles: dict[Role, list[StrictStr]]
    items: list[RoleProfileResponse]


class RoleOptionsResponse(BaseModel):
    items: list[RoleProfileResponse]


class ManagedAccountResponse(BaseModel):
    id: StrictInt
    username: StrictStr
    role: Role
    operator_id: StrictInt | None
    enabled: StrictBool
    must_change_password: StrictBool
    role_profile_id: StrictInt | None
    version: int = Field(ge=1)
    allowed_districts: list[StrictStr] | None
    permissions: list[StrictStr]


class ManagedAccountList(BaseModel):
    items: list[ManagedAccountResponse]
    total: int = Field(ge=0)
