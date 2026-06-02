"""Schemas for the books (new-arrivals / Gogoliana) flow."""

from pydantic import BaseModel

from gogol_cli.exhibition.schemas import BibInfo


class ParsedBookEntry(BaseModel):
    """A book entry parsed from the joint docx, without an image assigned yet."""

    bib: BibInfo
    description: str
    preview_text: str
    sort: int
