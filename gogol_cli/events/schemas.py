"""Schemas for event parsing."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ParsedEvent(BaseModel):
    """An event parsed from a single docx file."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    date_times: list[datetime]  # All occurrences, may have one or more
    description: str  # HTML format
    price: str
    purchase_link: str
    registration_link: str
    tags: str
    address: str  # Event address (extracted or inferred)
    is_active: bool = True  # Whether event should be active
    image_data: bytes | None
    image_filename: str | None
