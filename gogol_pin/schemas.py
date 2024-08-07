"""Schemas."""

from datetime import datetime
from pydantic import BaseModel


class Event(BaseModel):
    """Event schema."""

    id: int
    name: str
    active_from: datetime
    active_to: datetime
    preview_picture: int | None
    detail_picture: int | None
    detail_text: str | None
    detail_text_type: str

    @property
    def url(self) -> str:
        """Get event URL."""
        return f"https://www.domgogolya.ru/recital/{self.id}/"
