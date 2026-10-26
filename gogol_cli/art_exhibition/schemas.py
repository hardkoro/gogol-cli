"""Schemas for the art exhibition flow."""

from datetime import datetime

from pydantic import BaseModel


class ParsedArtExhibition(BaseModel):
    """An art exhibition entry parsed from a single doc/docx file.

    Images are assigned separately, after this is parsed.
    """

    title: str
    detail_text: str
    preview_text: str
    exhibit_from: datetime | None = None
    exhibit_to: datetime | None = None
