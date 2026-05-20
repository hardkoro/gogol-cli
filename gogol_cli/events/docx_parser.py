"""Parse event data from a .docx file."""

from __future__ import annotations

import os
import re
import zipfile
from datetime import datetime
from xml.etree import ElementTree as ET

import typer

from gogol_cli.events.schemas import ParsedEvent

_WNS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_RUSSIAN_MONTHS: dict[str, int] = {
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


# ---------------------------------------------------------------------------
# Text and formatting helpers
# ---------------------------------------------------------------------------


def _collapse_spaces(text: str) -> str:
    """Replace non-breaking spaces, then collapse runs of spaces to one."""
    text = text.replace("\xa0", " ")
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def _get_formatted_text(p: ET.Element) -> str:
    """Extract paragraph text preserving bold and italic formatting as HTML.

    Returns text with <b>, <i>, and <u> tags for formatting.
    """
    ns = {"w": _WNS}
    result: list[str] = []

    for run in p.findall(".//w:r", ns):
        # Check for formatting properties
        rPr = run.find(".//w:rPr", ns)
        is_bold = False
        is_italic = False
        is_underline = False

        if rPr is not None:
            is_bold = rPr.find(".//w:b", ns) is not None
            is_italic = rPr.find(".//w:i", ns) is not None
            is_underline = rPr.find(".//w:u", ns) is not None

        # Get text
        text_elem = run.find(".//w:t", ns)
        if text_elem is not None and text_elem.text:
            text = text_elem.text

            if is_bold:
                text = f"<b>{text}</b>"
            if is_italic:
                text = f"<i>{text}</i>"
            if is_underline:
                text = f"<u>{text}</u>"

            result.append(text)

    combined = "".join(result)
    combined = _collapse_spaces(combined)
    return combined


def _get_paragraphs(tree: ET.ElementTree[ET.Element]) -> list[str]:
    """Extract collapsed, stripped, non-empty paragraph texts with formatting."""
    ns = {"w": _WNS}
    result = []
    for p in tree.findall(".//w:p", ns):
        text = _get_formatted_text(p)
        if text:
            result.append(text)
    return result


def _extract_image(zf: zipfile.ZipFile) -> tuple[bytes, str] | None:
    """Return (bytes, filename) for the first image found in a docx zip."""
    for name in zf.namelist():
        if name.startswith("word/media/"):
            return zf.read(name), os.path.basename(name)
    return None


# ---------------------------------------------------------------------------
# Date/time parsing
# ---------------------------------------------------------------------------


def _parse_date_time(line: str) -> datetime | None:
    """Parse a date/time line like '27 мая в 19:00' into a datetime.

    Returns None if parsing fails.
    """
    # Find Russian month
    month_match = None
    month_num = None
    for month_name, num in _RUSSIAN_MONTHS.items():
        if month_name in line:
            month_match = month_name
            month_num = num
            break

    if month_match is None or month_num is None:
        return None

    # Extract day
    day_match = re.search(r"(\d{1,2})\s+" + re.escape(month_match), line)
    if day_match is None:
        return None
    day = int(day_match.group(1))

    # Extract time (HH:MM or HH)
    time_match = re.search(r"в\s+(\d{1,2}):?(\d{2})?", line)
    hour = int(time_match.group(1)) if time_match else 0
    minute = int(time_match.group(2)) if time_match and time_match.group(2) else 0

    # Use current year; if date is in the past, use next year
    today = datetime.now().date()
    candidate = datetime(today.year, month_num, day, hour, minute)
    if candidate.date() < today:
        candidate = datetime(today.year + 1, month_num, day, hour, minute)

    return candidate


def _paragraphs_to_html(paragraphs: list[str]) -> str:
    """Convert a list of paragraphs to HTML."""
    result = []
    for paragraph in paragraphs:
        if paragraph:  # Skip empty paragraphs
            # Always wrap normal paragraph content into <p>; preserve already-wrapped blocks.
            wrapped = paragraph.strip()
            if not (wrapped.startswith("<p") and wrapped.endswith("</p>")):
                wrapped = f"<p>{wrapped}</p>"
            result.append(wrapped)
    return "\n".join(result)


def _strip_html_tags(text: str) -> str:
    """Remove HTML tags and normalize spacing for plain-text fields."""
    clean = re.sub(r"<[^>]+>", "", text)
    return _collapse_spaces(clean)


# ---------------------------------------------------------------------------
# Price and link extraction
# ---------------------------------------------------------------------------


def _extract_price(line: str) -> str | None:
    """Extract price from a line like 'Стоимость билета: 2000 руб.'"""
    match = re.search(r"(\d+(?:\s*-\s*\d+)?)\s*(?:руб|руб\.)?", line, re.IGNORECASE)
    return match.group(1) if match else None


def _extract_link(line: str) -> str | None:
    """Extract URL from a line."""
    match = re.search(r"https?://\S+", line)
    return match.group(0) if match else None


def _is_registration_line(line: str) -> bool:
    """Return True when line indicates registration rather than ticket purchase."""
    lower = line.lower()
    return "регистрац" in lower


def _default_mode_tag(name: str) -> str:
    """Return default mode tag based on event title."""
    return "Онлайн" if "онлайн" in name.lower() else "Офлайн"


# ---------------------------------------------------------------------------
# Interactive prompts
# ---------------------------------------------------------------------------


def _prompt_event_details(parsed: ParsedEvent) -> ParsedEvent:
    """Prompt user to confirm/adjust parsed event details."""
    typer.echo("\n--- Event Details ---")

    name = _strip_html_tags(typer.prompt("Name", default=parsed.name))

    date_str = parsed.date_time.strftime("%Y-%m-%d")
    time_str = parsed.date_time.strftime("%H:%M")
    date_input = typer.prompt("Date (YYYY-MM-DD)", default=date_str)
    time_input = typer.prompt("Time (HH:MM)", default=time_str)
    try:
        date_time = datetime.strptime(f"{date_input} {time_input}", "%Y-%m-%d %H:%M")
    except ValueError:
        typer.echo("Invalid date format, keeping original")
        date_time = parsed.date_time

    typer.echo(f"\nDescription preview: {parsed.description[:200]}...")
    description = parsed.description  # Keep as-is, user can edit separately if needed

    price = typer.prompt("Price", default=parsed.price)
    purchase_link = typer.prompt("Purchase link", default=parsed.purchase_link)
    registration_link = typer.prompt("Registration link", default=parsed.registration_link)
    tags = typer.prompt("Tags (comma-separated)", default=parsed.tags)

    return parsed.model_copy(
        update={
            "name": name,
            "date_time": date_time,
            "description": description,
            "price": price,
            "purchase_link": purchase_link,
            "registration_link": registration_link,
            "tags": tags,
        }
    )


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------


def parse_event_file(file_path: str) -> ParsedEvent:
    """Parse a single event DOCX file into a ParsedEvent.

    Args:
        file_path: Path to the .docx file

    Returns:
        ParsedEvent with extracted data
    """
    with zipfile.ZipFile(file_path) as zf:
        with zf.open("word/document.xml") as f:
            tree = ET.parse(f)

        img_result = _extract_image(zf)

    paragraphs = _get_paragraphs(tree)
    if not paragraphs:
        raise ValueError(f"Empty event file: {file_path}")

    # Expected structure:
    # 0: name
    # 1: date and time
    # 2+: description paragraphs
    # ... price
    # ... purchase or registration link

    name = _strip_html_tags(paragraphs[0])

    date_time: datetime | None = None
    date_time_idx = 0
    if len(paragraphs) > 1:
        date_time = _parse_date_time(paragraphs[1])
        if date_time:
            date_time_idx = 1

    if date_time is None:
        raise ValueError(
            f"Could not parse date/time from: {paragraphs[1] if len(paragraphs) > 1 else 'N/A'}"
        )

    # Find price and links (they're typically at the end)
    price = ""
    purchase_link = ""
    registration_link = ""
    description_end_idx = len(paragraphs)

    for i in range(len(paragraphs) - 1, date_time_idx + 1, -1):
        para = paragraphs[i]

        if "https://" in para or "http://" in para:
            extracted_link = _extract_link(para)
            if extracted_link:
                if _is_registration_line(para):
                    registration_link = extracted_link
                else:
                    purchase_link = extracted_link
                description_end_idx = i
                continue

        if "билет" in para.lower() or "стоимость" in para.lower():
            extracted_price = _extract_price(para)
            if extracted_price:
                price = extracted_price
                description_end_idx = i
                continue

    # Description is everything between date/time and special fields
    description_paras = paragraphs[date_time_idx + 1 : description_end_idx]
    description_html = _paragraphs_to_html(description_paras)

    image_data, image_filename = img_result if img_result else (None, None)

    return ParsedEvent(
        name=name,
        date_time=date_time,
        description=description_html,
        price=price,
        purchase_link=purchase_link,
        registration_link=registration_link,
        tags=_default_mode_tag(name),
        image_data=image_data,
        image_filename=image_filename,
    )
