"""Parse event data from a .docx file."""

from __future__ import annotations

import os
import re
import zipfile
from datetime import datetime
from xml.etree import ElementTree as ET

import typer

from gogol_cli import constants as const
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

_MAX_DAY_IN_MONTH = 31
_MAX_HOUR = 23
_MAX_MINUTE = 59

# Venue-specific address mapping
_VENUE_ADDRESSES: dict[str, str] = {
    "Новое крыло": const.LECTURE_HALL_ADDRESS,
    "Лекторий": const.LECTURE_HALL_ADDRESS,
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

    # Validate day is in valid range
    if day < 1 or day > _MAX_DAY_IN_MONTH:
        return None

    # Extract time (HH:MM) — search after the month name; "в" is optional
    after_month = line[day_match.end() :]
    time_match = re.search(r"(?:в\s+)?(\d{1,2}):(\d{2})", after_month)
    if time_match is None:
        return None

    hour = int(time_match.group(1))
    minute = int(time_match.group(2))

    # Validate hour and minute are in valid ranges
    if hour < 0 or hour > _MAX_HOUR or minute < 0 or minute > _MAX_MINUTE:
        return None

    # Use current year; if date is in the past, use next year
    today = datetime.now().date()
    candidate = datetime(today.year, month_num, day, hour, minute)
    if candidate.date() < today:
        candidate = datetime(today.year + 1, month_num, day, hour, minute)

    return candidate


def _parse_multiple_date_times(line: str) -> list[datetime]:
    """Parse multiple dates/times from a line like '16, 17, 18 июня в 12:00' or '30 мая в 14:00 и 16:00'.

    Returns list of datetime objects for all day × time combinations found.
    """
    date_times: list[datetime] = []

    # Find Russian month
    month_match = None
    month_num = None
    for month_name, num in _RUSSIAN_MONTHS.items():
        if month_name in line:
            month_match = month_name
            month_num = num
            break

    if month_match is None or month_num is None:
        return date_times

    # Extract all day numbers before the month name (handles "16, 17, 18 июня" and "18 июня")
    month_pos = line.index(month_match)
    before_month = line[:month_pos]
    days = [
        int(m) for m in re.findall(r"\d{1,2}", before_month) if 1 <= int(m) <= _MAX_DAY_IN_MONTH
    ]

    if not days:
        return date_times

    # Extract the time section after the month name; "в" is optional
    after_month = line[month_pos + len(month_match) :]
    time_section_match = re.search(r"(?:в\s+)?(\d.+)$", after_month)
    if time_section_match is None:
        return date_times
    time_section = time_section_match.group(1)

    # Extract all times as HH:MM pairs (require colon to avoid spurious matches)
    times: list[tuple[int, int]] = []
    for time_match in re.finditer(r"(\d{1,2}):(\d{2})", time_section):
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
        if 0 <= hour <= _MAX_HOUR and 0 <= minute <= _MAX_MINUTE:
            times.append((hour, minute))

    if not times:
        return date_times

    today = datetime.now().date()
    for day in days:
        for hour, minute in times:
            candidate = datetime(today.year, month_num, day, hour, minute)
            if candidate.date() < today:
                candidate = datetime(today.year + 1, month_num, day, hour, minute)
            date_times.append(candidate)

    return date_times


def _extract_address(paragraphs: list[str]) -> tuple[str | None, list[str]]:
    """Extract address from 'Место встречи:' line and return cleaned paragraphs.

    Returns tuple of (address, filtered_paragraphs).
    """
    address = None
    filtered = []

    for para in paragraphs:
        if "место встречи" in para.lower():
            # Extract the address part (everything after "Место встречи:")
            match = re.search(
                r"(?:место встречи|место встреч[^:]*)[:\s]+(.+?)(?:<|$)",
                para,
                re.IGNORECASE | re.DOTALL,
            )
            if match:
                address = _strip_html_tags(match.group(1)).strip().rstrip(".")
            # Don't include this paragraph in the description
        else:
            filtered.append(para)

    return address, filtered


def _strip_trailing_colon(text: str) -> str:
    """Strip a trailing colon (with optional whitespace) left by a removed link paragraph."""
    return re.sub(r":\s*$", "", text)


def _paragraphs_to_html(paragraphs: list[str]) -> str:
    """Convert a list of paragraphs to HTML."""
    result = []
    for paragraph in paragraphs:
        if paragraph:  # Skip empty paragraphs
            wrapped = _strip_trailing_colon(paragraph.strip())
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


def _is_registration_line(line: str, context: str = "") -> bool:
    """Return True when line or surrounding context indicates registration rather than ticket purchase."""
    return "регистрац" in (line + " " + context).lower()


def _default_mode_tag(name: str) -> str:
    """Return default mode tag based on event title."""
    return "Онлайн" if "онлайн" in name.lower() else "Офлайн"


def _build_default_tags(name: str, price: str) -> str:
    """Return default comma-separated tags, appending 'Бесплатно' when price is empty."""
    tags = _default_mode_tag(name)
    if not price:
        tags += ",Бесплатно"
    return tags


# ---------------------------------------------------------------------------
# Interactive prompts
# ---------------------------------------------------------------------------


def _prompt_event_details(parsed: ParsedEvent) -> ParsedEvent:
    """Prompt user to confirm/adjust parsed event details."""
    typer.echo("\n--- Event Details ---")

    name = _strip_html_tags(typer.prompt("Name", default=parsed.name))

    date_str = parsed.date_times[0].strftime("%Y-%m-%d")
    time_str = parsed.date_times[0].strftime("%H:%M")
    date_input = typer.prompt("Date (YYYY-MM-DD)", default=date_str)
    time_input = typer.prompt("Time (HH:MM)", default=time_str)
    try:
        first_date_time = datetime.strptime(f"{date_input} {time_input}", "%Y-%m-%d %H:%M")
    except ValueError:
        typer.echo("Invalid date format, keeping original")
        first_date_time = parsed.date_times[0]

    # Preserve all occurrences; if the date changed, shift the rest to the new date
    new_date = first_date_time.date()
    original_date = parsed.date_times[0].date()
    if new_date != original_date:
        date_times = [
            dt.replace(year=new_date.year, month=new_date.month, day=new_date.day)
            for dt in parsed.date_times
        ]
        date_times[0] = first_date_time
    else:
        date_times = [first_date_time, *parsed.date_times[1:]]

    typer.echo(f"\nDescription preview: {parsed.description[:200]}...")
    description = parsed.description  # Keep as-is, user can edit separately if needed

    price = typer.prompt("Price", default=parsed.price)
    purchase_link = typer.prompt("Purchase link", default=parsed.purchase_link)
    registration_link = typer.prompt("Registration link", default=parsed.registration_link)
    tags = typer.prompt("Tags (comma-separated)", default=parsed.tags)

    address = typer.prompt("Address", default=parsed.address)

    return parsed.model_copy(
        update={
            "name": name,
            "date_times": date_times,
            "description": description,
            "price": price,
            "purchase_link": purchase_link,
            "registration_link": registration_link,
            "tags": tags,
            "address": address,
        }
    )


# ---------------------------------------------------------------------------
# Main parser helpers
# ---------------------------------------------------------------------------


def _extract_date_times(paragraphs: list[str]) -> tuple[list[datetime], int]:
    """Parse date/times from paragraphs, returning (date_times, date_time_idx)."""
    raw = paragraphs[1] if len(paragraphs) > 1 else ""
    date_time = _parse_date_time(raw)
    if date_time is None:
        raise ValueError(f"Could not parse date/time from: {raw or 'N/A'}")
    date_times = _parse_multiple_date_times(raw) or [date_time]
    return date_times, 1


def _scan_trailing_fields(paragraphs: list[str], start_idx: int) -> tuple[str, str, str, int]:
    """Scan paragraphs from the end for price and links.

    Returns (price, purchase_link, registration_link, description_end_idx).
    """
    price = ""
    purchase_link = ""
    registration_link = ""
    description_end_idx = len(paragraphs)

    for i in range(len(paragraphs) - 1, start_idx + 1, -1):
        para = paragraphs[i]
        if "https://" in para or "http://" in para:
            extracted_link = _extract_link(para)
            if extracted_link:
                prev_para = paragraphs[i - 1] if i > start_idx + 1 else ""
                if _is_registration_line(para, prev_para):
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

    return price, purchase_link, registration_link, description_end_idx


def _resolve_final_address(address: str | None, description_html: str, tail_text: str = "") -> str:
    """Return address: explicit > venue lookup > default."""
    if address:
        return address
    searchable = description_html + " " + tail_text
    for venue_name, venue_address in _VENUE_ADDRESSES.items():
        if venue_name in searchable:
            return venue_address
    return const.DEFAULT_ADDRESS


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

    name = _strip_html_tags(paragraphs[0])
    date_times, date_time_idx = _extract_date_times(paragraphs)
    price, purchase_link, registration_link, description_end_idx = _scan_trailing_fields(
        paragraphs, date_time_idx
    )

    description_paras = paragraphs[date_time_idx + 1 : description_end_idx]
    address, filtered_description_paras = _extract_address(description_paras)
    description_html = _paragraphs_to_html(filtered_description_paras)
    tail_text = " ".join(p for p in paragraphs[description_end_idx:] if p)
    final_address = _resolve_final_address(address, description_html, tail_text)

    image_data, image_filename = img_result if img_result else (None, None)

    return ParsedEvent(
        name=name,
        date_times=date_times,
        description=description_html,
        price=price,
        purchase_link=purchase_link,
        registration_link=registration_link,
        tags=_build_default_tags(name, price),
        address=final_address,
        is_active=True,
        image_data=image_data,
        image_filename=image_filename,
    )
