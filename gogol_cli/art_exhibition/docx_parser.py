"""Parse a single art exhibition entry from a .doc/.docx file."""

from __future__ import annotations

import re
from datetime import datetime

import typer

from gogol_cli.art_exhibition.schemas import ParsedArtExhibition
from gogol_cli.exhibition.docx_parser import _paragraphs_to_html
from gogol_cli.virtual_exhibition.parser import _get_paragraphs

_MONTHS_GENITIVE = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}
_MONTH_NAME_RE = "|".join(_MONTHS_GENITIVE)

# "7 — 25 октября 2026 года" or "28 сентября — 25 октября 2026 года"
_DATE_RANGE_RE = re.compile(
    rf"^(\d{{1,2}})(?:\s+(?:{_MONTH_NAME_RE}))?\s*[-–—]\s*"
    rf"(\d{{1,2}})\s+({_MONTH_NAME_RE})\s+(\d{{4}})\s*(?:г(?:ода)?\.?)?\s*$",
    re.IGNORECASE,
)


def _parse_date_range_line(text: str) -> tuple[datetime, datetime] | None:
    """Parse a line like '7 — 25 октября 2026 года' into (from, to) dates.

    The end date's month/year also apply to the start date unless the start
    date has its own month written out (e.g. '28 сентября — 25 октября 2026 года').
    """
    match = _DATE_RANGE_RE.match(text.strip())
    if match is None:
        return None

    from_day, to_day, to_month_name, year = match.groups()
    year_num = int(year)
    to_month = _MONTHS_GENITIVE[to_month_name.lower()]

    start_month_match = re.match(
        rf"^\d{{1,2}}\s+({_MONTH_NAME_RE})\b", text.strip(), re.IGNORECASE
    )
    from_month = (
        _MONTHS_GENITIVE[start_month_match.group(1).lower()] if start_month_match else to_month
    )

    return (
        datetime(year_num, from_month, int(from_day)),
        datetime(year_num, to_month, int(to_day)),
    )


def _prompt_title(title: str) -> str:
    typer.echo(f"\nParsed art exhibition title: {title}")
    return typer.prompt("Exhibition title", default=title)


def parse_art_exhibition_file(path: str) -> ParsedArtExhibition:
    """Parse a .doc/.docx file into a ParsedArtExhibition.

    Structure detected:
    - Para 0: title (becomes the element name)
    - Para 1 (optional): a date range line ('7 — 25 октября 2026 года'), consumed and
      not included in the body
    - Remaining paras: body <p> elements; the first one doubles as the preview text.

    Displays an interactive prompt so the user can confirm or correct the title.
    """
    paragraphs = _get_paragraphs(path)
    if not paragraphs:
        raise ValueError(f"Empty document: {path}")

    title = _prompt_title(paragraphs[0])

    body = paragraphs[1:]
    exhibit_from: datetime | None = None
    exhibit_to: datetime | None = None
    if body:
        date_range = _parse_date_range_line(body[0])
        if date_range is not None:
            exhibit_from, exhibit_to = date_range
            body = body[1:]

    detail_text = _paragraphs_to_html(body)
    preview_text = f"<p>{body[0]}</p>" if body else ""

    return ParsedArtExhibition(
        title=title,
        detail_text=detail_text,
        preview_text=preview_text,
        exhibit_from=exhibit_from,
        exhibit_to=exhibit_to,
    )
