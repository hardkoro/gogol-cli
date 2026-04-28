"""Parse a virtual exhibition from a folder containing a doc/docx and image files."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime
from xml.etree import ElementTree as ET

import typer

from gogol_cli.virtual_exhibition.schemas import (
    ParsedVirtualExhibition,
    ParsedVirtualExhibitionItem,
)

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W_NSMAP = {"w": _W_NS}

# ---------------------------------------------------------------------------
# Helpers – text cleaning
# ---------------------------------------------------------------------------

_KP_RE = re.compile(r"КП[-\s]+(\d+)", re.IGNORECASE)
_BIB_INDEX_RE = re.compile(r"^[А-ЯЁA-Z]{1,3}-\d+\s*$")
_DATE_DD_MM_YYYY = re.compile(r"\b(\d{1,2})\.(\d{2})\.(\d{4})\b")
_DATE_DD_MM_DOT = re.compile(r"\b(\d{1,2})\.(\d{2})\.(?=\s*[-\u2013\u2014]|\s*$)")

# Thresholds used in heuristic checks
_MIN_WORD_LEN = 4
_LONG_WORD_THRESHOLD = 30
_MIN_CYRILLIC_RATIO = 0.10
_MIN_DATE_MATCHES = 2
_MAX_BIB_ORIGIN_LEN = 80
_MAX_BIB_MATERIAL_LEN = 100
_MAX_ITEM_NAME_LEN = 100
_MAX_DATE_CONTINUATION_LEN = 40


def _collapse_spaces(text: str) -> str:
    text = text.replace("\xa0", " ")
    return re.sub(r" {2,}", " ", text).strip()


def _is_garbage(text: str) -> bool:
    """Return True if the paragraph looks like RTF binary/format garbage."""
    stripped = text.strip()
    if not stripped:
        return True
    if len(stripped) < _MIN_WORD_LEN:
        return False
    cyrillic = sum(1 for c in stripped if "\u0400" <= c <= "\u04ff")
    if len(stripped) > _LONG_WORD_THRESHOLD and cyrillic / len(stripped) < _MIN_CYRILLIC_RATIO:
        return True
    return False


_BIB_ORIGIN_RE = re.compile(
    r"^(СССР|Россия|РСФСР|Франция|Германия|Англия|[А-ЯЁ][а-яё]+)\b.*\b(19|20)\d{2}\b",
    re.IGNORECASE,
)
_BIB_MATERIAL_KEYWORDS = (
    "бумага",
    "холст",
    "картон",
    "ксилография",
    "гравюра",
    "тушь",
    "акварель",
    "масло",
    "карандаш",
    "белила",
    "офорт",
    "литография",
    "шёлк",
    "ткань",
    "пергамент",
    "металл",
    "дерево",
    "линогравюра",
)
_BIB_SUMMARY_RE = re.compile(r"^всего\s+предметов", re.IGNORECASE)


def _is_bib_origin(text: str) -> bool:
    """Return True if a line looks like an origin/year line (e.g. "СССР. 1952").

    Real origin lines are short (country + year).  Long sentences that happen to
    start with a Cyrillic word and contain a year-like number are not origins.
    """
    t = text.strip()
    return len(t) < _MAX_BIB_ORIGIN_LEN and bool(_BIB_ORIGIN_RE.match(t))


def _is_bib_material(text: str) -> bool:
    """Return True if a line lists materials/technique (a bib line, not description text).

    Real bib material lines are short (e.g. "Бумага, ксилография. 29,9х22,1 см.").
    Long description sentences that mention the same keywords are excluded.
    """
    if len(text.strip()) >= _MAX_BIB_MATERIAL_LEN:
        return False
    low = text.lower()
    return any(kw in low for kw in _BIB_MATERIAL_KEYWORDS)


def _is_item_terminator(text: str) -> bool:
    """Return True for trailing summary lines like 'Всего предметов: 7'."""
    return bool(_BIB_SUMMARY_RE.match(text.strip()))


def _extract_kp_number(text: str) -> int | None:
    """Return the КП inventory number from a text string, or None."""
    m = _KP_RE.search(text)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Date extraction
# ---------------------------------------------------------------------------


def _parse_date_dmy(day: str, month: str, year: str) -> datetime:
    return datetime(int(year), int(month), int(day))


def _extract_dates(text: str) -> tuple[datetime | None, datetime | None]:
    """Return (active_from, active_to) parsed from a text string, or Nones."""
    full_matches = list(_DATE_DD_MM_YYYY.finditer(text))
    if len(full_matches) >= _MIN_DATE_MATCHES:
        d1 = _parse_date_dmy(*full_matches[0].groups())
        d2 = _parse_date_dmy(*full_matches[1].groups())
        return d1, d2
    if len(full_matches) == 1:
        # Check for an incomplete first date like "30.04. –30.06.2026"
        # The year from the complete date is used for both.
        year = full_matches[0].group(3)
        partial = _DATE_DD_MM_DOT.search(text)
        if partial and partial.start() < full_matches[0].start():
            d1 = _parse_date_dmy(partial.group(1), partial.group(2), year)
            d2 = _parse_date_dmy(*full_matches[0].groups())
            return d1, d2
        return _parse_date_dmy(*full_matches[0].groups()), None
    return None, None


def _strip_dates(text: str) -> str:
    """Remove date patterns from the end of a title string."""
    # Handle "DD.MM. –DD.MM.YYYY" style (dash-separated partial + full date)
    cleaned = re.sub(
        r"\s+\d{1,2}\.\d{2}\.?\s*[-\u2013\u2014]\s*\d{1,2}\.\d{2}\.?\s*\d{0,4}\s*$",
        "",
        text,
    )
    # Handle plain "DD.MM.YYYY" or "DD.MM." at end (run twice for two-date plain form)
    cleaned = re.sub(r"\s+\d{1,2}\.\d{2}\.?\s*\d{0,4}\s*$", "", cleaned)
    cleaned = re.sub(r"\s+\d{1,2}\.\d{2}\.?\s*\d{0,4}\s*$", "", cleaned)
    return cleaned.strip()


# ---------------------------------------------------------------------------
# DOCX parser
# ---------------------------------------------------------------------------


def _parse_docx_paragraphs(path: str) -> list[str]:
    """Parse a .docx file and return plain text per paragraph."""
    with zipfile.ZipFile(path) as zf:
        with zf.open("word/document.xml") as f:
            tree = ET.parse(f)

    ns = _W_NSMAP
    results: list[str] = []

    for p in tree.findall(".//w:p", ns):
        chars: list[str] = []
        for r in p.findall("w:r", ns):
            for t in r.findall("w:t", ns):
                chars.extend(t.text or "")

        text = _collapse_spaces("".join(chars))
        if text:
            results.append(text)

    return results


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def _convert_doc_to_docx(path: str) -> str:
    """Convert a .doc/.rtf file to .docx via macOS textutil and return the new path.

    The converted file is placed in a temporary directory.  The caller is
    responsible for deleting that directory when done.
    """
    tmp_dir = tempfile.mkdtemp()
    base = os.path.splitext(os.path.basename(path))[0]
    converted = os.path.join(tmp_dir, base + ".docx")
    try:
        subprocess.run(
            ["textutil", "-convert", "docx", "-output", converted, path],
            check=True,
            capture_output=True,
        )
    except FileNotFoundError:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError("'textutil' not found. This conversion requires macOS.")
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError(
            f"textutil conversion failed for {path!r}: {exc.stderr.decode(errors='replace')}"
        ) from exc
    return converted


def _get_paragraphs(path: str) -> list[str]:
    """Parse a .docx or .doc/.rtf file, returning plain text per paragraph.

    .doc and .rtf files are first converted to .docx via macOS textutil so that
    all text (including special Unicode characters like en-dashes) is preserved
    correctly.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        return _parse_docx_paragraphs(path)
    # .doc / .rtf — convert to .docx first, then parse
    converted = _convert_doc_to_docx(path)
    tmp_dir = os.path.dirname(converted)
    try:
        return _parse_docx_paragraphs(converted)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Document structure parsing
# ---------------------------------------------------------------------------


class _RawItem:
    """Intermediate representation of one exhibition item before interactive prompts."""

    def __init__(self) -> None:
        self.name_lines: list[str] = []
        self.bib_lines: list[str] = []
        self.desc_lines: list[str] = []
        self.kp_number: int | None = None


def _is_bib_index(text: str) -> bool:
    """Return True for standalone archival index lines like 'Г-643' or 'КД-381'."""
    return bool(_BIB_INDEX_RE.match(text.strip()))


def _is_bib_line(text: str) -> bool:
    """Return True if this line looks like exhibition bib information."""
    return (
        _is_bib_origin(text)
        or _is_bib_material(text)
        or bool(_KP_RE.search(text))
        or _is_bib_index(text)
    )


def _looks_like_item_name(text: str) -> bool:
    """Return True if a line looks like an item name rather than body/description text.

    Names tend to be short (< 100 chars) and don't begin with a lowercase letter.
    """
    t = text.strip()
    return bool(t) and len(t) < _MAX_ITEM_NAME_LEN and not t[0].islower()


def _split_pending(
    pending: list[str],
) -> tuple[list[str], list[str]]:
    """Split a buffer of non-bib lines into (pre_lines, name_lines).

    Scans backward from the end of *pending*: consecutive lines that look like
    item names are peeled off as *name_lines*; the rest become *pre_lines*
    (body paragraphs or description continuation).
    """
    i = len(pending)
    while i > 0 and _looks_like_item_name(pending[i - 1]):
        i -= 1
    return pending[:i], pending[i:]


def _parse_document(  # noqa: PLR0912, PLR0915
    paragraphs: list[str],
) -> tuple[str, str, datetime | None, datetime | None, list[str], list[_RawItem]]:
    """Parse document paragraphs into exhibition header info and raw items.

    Returns:
        header_name: raw title string (may contain dates at end)
        raw_subtitle: empty string (subtitle is split interactively from title)
        active_from: parsed or None
        active_to: parsed or None
        body_paragraphs: list of body paragraphs
        raw_items: list of _RawItem
    """
    # Filter garbage
    clean = [t for t in paragraphs if not _is_garbage(t)]

    # -- Header: skip "Виртуальная выставка" line --
    idx = 0
    if idx < len(clean) and re.search(r"виртуальная\s+выставка", clean[idx], re.IGNORECASE):
        idx += 1

    # -- Title: take the next 1–2 paragraphs --
    # Always take the first paragraph as title.  If the line that immediately
    # follows is short (≤ 40 chars, typically a standalone date), include it too
    # so that date extraction works on the combined string.
    title_parts: list[str] = []
    while (
        idx < len(clean) and not _is_bib_line(clean[idx]) and not _is_item_terminator(clean[idx])
    ):
        text = clean[idx]
        title_parts.append(text)
        idx += 1
        if len(title_parts) == 1:
            # Take a second line only if it looks like a short date continuation
            if (
                idx < len(clean)
                and len(clean[idx].strip()) < _MAX_DATE_CONTINUATION_LEN
                and not _is_bib_line(clean[idx])
            ):
                continue  # will pick up line 2 on the next iteration
            else:
                break
        else:
            break

    raw_title = " ".join(title_parts)
    raw_title = re.sub(r"^[^А-ЯЁа-яёA-Za-z«»\"]+", "", raw_title).strip()
    active_from, active_to = _extract_dates(raw_title)
    raw_title_clean = _strip_dates(raw_title)

    # -- Body + Items --
    # We buffer non-bib lines in *pending*.  When we encounter an origin line
    # (e.g. "СССР. 1952"), we know a new item's bib section is starting.  At
    # that point we split *pending* into:
    #   • pre_lines  – long description/body sentences → assigned to previous
    #                  item's desc_lines (or body if no item yet)
    #   • name_lines – short title-case lines at the tail → assigned to the
    #                  new item's name_lines
    body: list[str] = []
    raw_items: list[_RawItem] = []
    current: _RawItem | None = None
    in_bib = False
    pending: list[str] = []

    while idx < len(clean):
        text = clean[idx]
        idx += 1

        if _is_item_terminator(text):
            break

        if _is_bib_origin(text):
            # --- Start of a new item's bib section ---
            pre, name_lines = _split_pending(pending)
            pending = []

            if current is None:
                body.extend(pre)
            else:
                current.desc_lines.extend(pre)

            current = _RawItem()
            raw_items.append(current)
            current.name_lines = name_lines

            kp = _extract_kp_number(text)
            if kp is not None:
                current.kp_number = kp
            current.bib_lines.append(text)
            in_bib = True

        elif _is_bib_line(text):
            # Non-origin bib line (material, КП number, dimensions …)
            if current is not None and in_bib:
                kp = _extract_kp_number(text)
                if kp is not None and current.kp_number is None:
                    current.kp_number = kp
                current.bib_lines.append(text)
            else:
                # Unexpected bib line before any item – treat as body/pending
                pending.append(text)

        else:
            # Non-bib line: could be body, item name, or description
            if current is not None and in_bib:
                # Bib section just ended – begin buffering desc / next-item name
                in_bib = False
            pending.append(text)

    # Flush remaining pending to last item's description (or body)
    if pending:
        if current is None:
            body.extend(pending)
        else:
            current.desc_lines.extend(pending)

    return raw_title_clean, "", active_from, active_to, body, raw_items


# ---------------------------------------------------------------------------
# Image loading
# ---------------------------------------------------------------------------


def _load_images(
    folder_path: str,
) -> tuple[tuple[bytes, str] | None, dict[int, list[tuple[bytes, str]]]]:
    """Load images from the exhibition folder.

    Returns:
        preview: (data, filename) for the non-КП preview image, or None
        kp_images: mapping from КП number → list of (data, filename)
    """
    image_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    preview: tuple[bytes, str] | None = None
    kp_images: dict[int, list[tuple[bytes, str]]] = {}

    for fname in sorted(os.listdir(folder_path)):
        ext = os.path.splitext(fname)[1].lower()
        if ext not in image_exts:
            continue
        kp_num = _extract_kp_number(fname)
        filepath = os.path.join(folder_path, fname)
        with open(filepath, "rb") as fh:
            data = fh.read()
        if kp_num is not None:
            kp_images.setdefault(kp_num, []).append((data, fname))
        elif preview is None:
            # Non-КП image — use as exhibition preview/detail
            preview = (data, fname)

    return preview, kp_images


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------


def _bib_lines_to_html(lines: list[str]) -> str:
    """Format bib info lines as a single HTML paragraph with line breaks."""
    if not lines:
        return ""
    inner = "<br />\n".join(line.strip() for line in lines if line.strip())
    return f"<p>{inner}</p>"


def _desc_lines_to_html(lines: list[str]) -> str:
    """Format description lines as HTML paragraphs."""
    paras = [line.strip() for line in lines if line.strip()]
    return "\n".join(f"<p>{p}</p>" for p in paras)


def _body_lines_to_html(lines: list[str]) -> tuple[str, str]:
    """Return (detail_text, preview_text) from body paragraphs."""
    paras = [line.strip() for line in lines if line.strip()]
    detail = "\n".join(f"<p>{p}</p>" for p in paras)
    preview = f"<p>{paras[0]}</p>" if paras else ""
    return detail, preview


# ---------------------------------------------------------------------------
# Interactive prompts
# ---------------------------------------------------------------------------

_VYSTAVKA_PREFIX = "Виртуальная выставка"


def _prompt_name_and_subtitle(raw_title: str) -> tuple[str, str]:
    """Interactively ask the user to confirm the exhibition name and subtitle.

    The raw_title is the full title from the document (without dates).
    Default split: first sentence becomes the short title; remainder is the subtitle.
    """
    typer.echo(f"\nRaw exhibition title from document:\n  {raw_title}")

    # Suggest a split at the first '. ' that is not at the very start
    dot_idx = raw_title.find(". ", 5)
    if dot_idx != -1:
        short = raw_title[:dot_idx]
        suggested_sub = raw_title[dot_idx + 2 :].strip()
    else:
        short = raw_title
        suggested_sub = ""

    # Wrap the short title in quotes and prepend the section name
    suggested_name = f"{_VYSTAVKA_PREFIX} \u00ab{short}\u00bb"

    typer.echo()
    name = typer.prompt("Exhibition name (prop NAME)", default=suggested_name)
    subtitle = typer.prompt("Exhibition subtitle (prop 196)", default=suggested_sub)
    return name, subtitle


def _prompt_dates(
    active_from: datetime | None,
    active_to: datetime | None,
) -> tuple[datetime, datetime]:
    """Ask the user to confirm or enter the active_from / active_to dates."""
    typer.echo()
    from_default = active_from.strftime("%d.%m.%Y") if active_from else ""
    to_default = active_to.strftime("%d.%m.%Y") if active_to else ""

    while True:
        from_str = typer.prompt("Active from (DD.MM.YYYY)", default=from_default)
        try:
            confirmed_from = datetime.strptime(from_str, "%d.%m.%Y")
            break
        except ValueError:
            typer.echo("  Invalid date format, please use DD.MM.YYYY")

    while True:
        to_str = typer.prompt("Active to (DD.MM.YYYY)", default=to_default)
        try:
            confirmed_to = datetime.strptime(to_str, "%d.%m.%Y")
            break
        except ValueError:
            typer.echo("  Invalid date format, please use DD.MM.YYYY")

    return confirmed_from, confirmed_to


def _prompt_item_name(raw_name: str, kp_number: int | None, item_index: int) -> str:
    """Ask the user to confirm or enter the item name."""
    typer.echo(f"\n--- Item {item_index} (КП {kp_number}) ---")
    default_name = raw_name if raw_name else (f"КП {kp_number}" if kp_number else "")
    return typer.prompt("  Item name (prop 197)", default=default_name)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def parse_virtual_exhibition_folder(folder_path: str) -> ParsedVirtualExhibition:
    """Parse a folder into a ParsedVirtualExhibition with interactive prompts.

    The folder must contain:
    - Exactly one .doc or .docx document file
    - One non-КП image (preview/detail for the whole exhibition)
    - One or more КП images (one per item or multiple per item sharing the КП number)
    """
    # -- Find the document file --
    doc_path: str | None = None
    for fname in os.listdir(folder_path):
        ext = os.path.splitext(fname)[1].lower()
        if ext in (".doc", ".docx", ".rtf") and not fname.startswith("~$"):
            doc_path = os.path.join(folder_path, fname)
            break
    if doc_path is None:
        raise ValueError(f"No .doc/.docx file found in: {folder_path}")

    # -- Parse document --
    paragraphs = _get_paragraphs(doc_path)
    raw_title, _, active_from, active_to, body_paras, raw_items = _parse_document(paragraphs)

    # -- Load images --
    preview_image, kp_images = _load_images(folder_path)
    if preview_image is None:
        raise ValueError("No preview image found (expected a non-КП image file in the folder).")
    if not kp_images:
        raise ValueError("No КП images found in the folder.")

    # -- Warn if item / image count mismatch --
    n_items = len(raw_items)
    n_kp = len(kp_images)
    if n_items != n_kp:
        typer.echo(
            f"\nWarning: document has {n_items} items but folder has {n_kp} КП image groups."
        )

    # -- Interactive: name + subtitle --
    name, subtitle = _prompt_name_and_subtitle(raw_title)

    # -- Interactive: dates --
    confirmed_from, confirmed_to = _prompt_dates(active_from, active_to)

    # -- Body text HTML --
    detail_text, _ = _body_lines_to_html(body_paras)
    preview_text = f"<p>{subtitle}</p>" if subtitle else ""

    # -- Build items --
    # Sort КП image groups by number for deterministic matching
    sorted_kp = sorted(kp_images.items())  # list of (kp_number, [(data, fname), ...])

    # Match items to images by КП number if possible, otherwise by order
    items: list[ParsedVirtualExhibitionItem] = []
    for item_idx, raw_item in enumerate(raw_items, start=1):
        # Determine images for this item
        if raw_item.kp_number is not None and raw_item.kp_number in kp_images:
            imgs = kp_images[raw_item.kp_number]
        elif item_idx - 1 < len(sorted_kp):
            imgs = sorted_kp[item_idx - 1][1]
        else:
            imgs = []
            typer.echo(f"\nWarning: no images found for item {item_idx}.")

        # Collect name from bold name_lines, stripping a leading index number
        raw_name = " ".join(raw_item.name_lines).strip()
        raw_name = re.sub(r"^\d+\s+", "", raw_name)
        confirmed_name = _prompt_item_name(raw_name, raw_item.kp_number, item_idx)

        bib_html = _bib_lines_to_html(raw_item.bib_lines)
        desc_html = _desc_lines_to_html(raw_item.desc_lines)

        items.append(
            ParsedVirtualExhibitionItem(
                name=confirmed_name,
                bib_text=bib_html,
                description=desc_html,
                kp_number=raw_item.kp_number,
                images=imgs,
            )
        )

    return ParsedVirtualExhibition(
        title=name,
        subtitle=subtitle,
        preview_text=preview_text,
        detail_text=detail_text,
        active_from=confirmed_from,
        active_to=confirmed_to,
        preview_image_data=preview_image[0],
        preview_image_filename=preview_image[1],
        items=items,
    )
