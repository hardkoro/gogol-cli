"""Schemas for virtual exhibition parsing."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ParsedVirtualExhibitionItem(BaseModel):
    """One item (artifact/object) parsed from the virtual exhibition document."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    bib_text: str  # HTML for prop 198 (origin, materials, indices joined)
    description: str  # HTML for prop 199 (optional editorial description)
    kp_number: int | None  # КП inventory number extracted from the bib indices line
    images: list[tuple[bytes, str]]  # (image_bytes, filename) for prop 200


class ParsedVirtualExhibition(BaseModel):
    """A virtual exhibition parsed from a folder."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    title: str  # Element NAME, e.g. 'Виртуальная выставка «...»'
    subtitle: str  # Prop 196 – short descriptive sub-header
    preview_text: str  # HTML preview text (first body paragraph)
    detail_text: str  # HTML detail text (all body paragraphs)
    active_from: datetime
    active_to: datetime
    preview_image_data: bytes
    preview_image_filename: str
    items: list[ParsedVirtualExhibitionItem]
