"""Schemas for the news flow."""

from pydantic import BaseModel


class ParsedNews(BaseModel):
    """A news entry parsed from a single docx file, without images assigned yet."""

    title: str
    detail_text: str
    preview_text: str
