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
