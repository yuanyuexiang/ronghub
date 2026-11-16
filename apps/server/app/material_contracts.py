"""Private application and inspection material metadata, without storage locators."""

from pydantic import BaseModel, Field


class ApplicationMaterialItem(BaseModel):
    id: int
    filename: str
    content_type: str
    size: int = Field(ge=0)
    sha256: str
    created_at: str


class ApplicationMaterialList(BaseModel):
    items: list[ApplicationMaterialItem]


class InspectionMaterialItem(ApplicationMaterialItem):
    action_id: int | None


class InspectionMaterialList(BaseModel):
    items: list[InspectionMaterialItem]


def binary_responses(*types):
    return {
        200: {
            "content": {mime: {"schema": {"type": "string", "format": "binary"}} for mime in types}
        }
    }
