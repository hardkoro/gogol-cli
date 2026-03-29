"""Schemas."""

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class Event(BaseModel):
    """Event schema."""

    id: int
    name: str
    active_from: datetime
    active_to: datetime
    preview_picture: int | None
    preview_text: str | None
    preview_text_type: str
    detail_picture: int | None
    detail_text: str | None
    detail_text_type: str
    tags: str | None

    @property
    def url(self) -> str:
        """Get event URL."""
        return f"https://www.domgogolya.ru/recital/{self.id}/"

    @property
    def active_to_hours(self) -> str:
        """Get active to hours."""
        return self.active_to.strftime("%H")

    @property
    def active_to_minutes(self) -> str:
        """Get active to minutes."""
        return self.active_to.strftime("%M")


class File(BaseModel):
    """File schema."""

    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(alias="ID")
    timestamp: datetime = Field(alias="TIMESTAMP_X")
    module_id: str = Field(alias="MODULE_ID")
    height: int = Field(alias="HEIGHT")
    width: int = Field(alias="WIDTH")
    file_size: int = Field(alias="FILE_SIZE")
    content_type: str = Field(alias="CONTENT_TYPE")
    subdir: str = Field(alias="SUBDIR")
    file_name: str = Field(alias="FILE_NAME")
    original_name: str | None = Field(alias="ORIGINAL_NAME")
    external_id: str | None = Field(alias="EXTERNAL_ID")
