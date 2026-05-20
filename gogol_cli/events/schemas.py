"""Schemas for event parsing."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ParsedEvent(BaseModel):
    """An event parsed from a single docx file."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    date_time: datetime
    description: str  # HTML format
    price: str
    purchase_link: str
    registration_link: str
    tags: str
    image_data: bytes | None
    image_filename: str | None
