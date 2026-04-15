"""Schemas for exhibition parsing."""

from pydantic import BaseModel, ConfigDict


class BibInfo(BaseModel):
    """Parsed bibliographic information for a book."""

    title: str
    author: str
    city: str
    publisher: str
    year: str
    full_text: str


class ParsedBook(BaseModel):
    """A book parsed from a single docx file."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    cover_data: bytes
    cover_filename: str
    bib: BibInfo
    description: str
    preview_text: str
    sort: int


class ParsedExhibition(BaseModel):
    """An exhibition parsed from a folder of docx files."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    title: str
    detail_text: str
    preview_text: str
    illustration_data: bytes
    illustration_filename: str
    books: list[ParsedBook]
