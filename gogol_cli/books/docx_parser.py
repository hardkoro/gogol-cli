"""Parse multiple book entries from a single joint .doc/.docx file."""

from __future__ import annotations

import os
import re
import zipfile
from xml.etree import ElementTree as ET

import typer

from gogol_cli.books.schemas import ParsedBookEntry
from gogol_cli.exhibition.docx_parser import (
    _collapse_spaces,
    _first_sentence,
    _paragraphs_to_html,
)
from gogol_cli.exhibition.schemas import BibInfo
from gogol_cli.virtual_exhibition.parser import _convert_doc_to_docx

_WNS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_MAX_AUTHOR_WORDS = 6  # "Lastname, Firstname Patronymic" is at most this many tokens


# ---------------------------------------------------------------------------
# Paragraph helpers
# ---------------------------------------------------------------------------


def _get_paragraphs(tree: ET.ElementTree[ET.Element]) -> list[str]:
    ns = {"w": _WNS}
    result = []
    for p in tree.findall(".//w:p", ns):
        runs = p.findall(".//w:t", ns)
        text = "".join(r.text or "" for r in runs)
        text = _collapse_spaces(text)
        if text:
            result.append(text)
    return result


def _is_numbered_bib(text: str) -> bool:
    """Return True if the paragraph opens with a book number (e.g. '1. Author...')."""
    return bool(re.match(r"^\d+\.\s+\S", text))


def _is_author_only_line(text: str) -> bool:
    """Detect a standalone author line: 'Lastname, Firstname [Patronymic] [(dates)].'"""
    if ". -" in text or " / " in text or " : " in text:
        return False
    return bool(re.match(r"^\w[\w\s\-]*,\s+\w", text))


def _is_bib_line(text: str) -> bool:
    """Detect a bib entry: responsibility marker ( / ) and place marker (. -)."""
    return " / " in text and ". - " in text


def _clean_author(raw: str) -> str:
    """Strip trailing period and life-date parenthetical from an author string."""
    author = raw.rstrip(".")
    return re.sub(r"\s*\(\d{4}-\d{4}\)\s*$", "", author).strip()


# ---------------------------------------------------------------------------
# Bib field extraction
# ---------------------------------------------------------------------------


def _parse_bib_fields(bib_line: str, author: str | None = None) -> BibInfo:
    """Extract bibliographic fields from a bib line.

    *author* is used as-is when provided (unnumbered format).
    When None the author field is left blank (caller handles it).
    """
    # Year: first publication-era 4-digit year.
    year_match = re.search(r"\b(1[89]\d{2}|20\d{2})\b", bib_line)
    year = year_match.group(1) if year_match else ""

    # City and publisher: ". - CITY : PUBLISHER, YEAR"
    # Allow hyphens in city names (Санкт-Петербург, Ростов-на-Дону).
    # Publisher may be "А : Б" (double-publisher format).
    city_pub_match = re.search(
        r"\.\s*-\s*([^:;\n]+?)\s*:\s*(.+?),\s*(?:1[89]\d{2}|20\d{2})\b",
        bib_line,
    )
    city = city_pub_match.group(1).strip() if city_pub_match else ""
    publisher = city_pub_match.group(2).strip() if city_pub_match else ""

    # Title: text before the first " / " (primary responsibility statement).
    title_match = re.match(r"^(.+?)\s+/\s+", bib_line)
    if not title_match:
        title_match = re.match(r"^(.+?)\.\s+-\s", bib_line)
    title = title_match.group(1).strip() if title_match else bib_line.split(". ")[0].strip()

    return BibInfo(
        title=title,
        author=author or "",
        city=city,
        publisher=publisher,
        year=year,
        full_text=bib_line,
    )


def _parse_numbered_bib(bib_line: str) -> BibInfo:
    """Parse a numbered bib line: strip number, extract embedded author, parse fields."""
    bib = re.sub(r"^\d+\.\s+", "", bib_line).strip()

    # Try to split "Lastname, Firstname [Patronymic] [(dates)]. Title / ..."
    author = ""
    rest = bib
    author_match = re.match(r"^([^.(]+,\s[^.(]+(?:\([^)]+\))?)\.\s+(.+)$", bib)
    if author_match:
        candidate = author_match.group(1).strip()
        if "," in candidate and len(candidate.split()) <= _MAX_AUTHOR_WORDS:
            author = re.sub(r"\s*\(\d{4}-\d{4}\)\s*$", "", candidate).strip()
            rest = author_match.group(2).strip()

    result = _parse_bib_fields(rest, author=author if author else None)
    return result.model_copy(update={"full_text": bib})


# ---------------------------------------------------------------------------
# Splitting strategies
# ---------------------------------------------------------------------------


def _split_numbered(
    paragraphs: list[str],
) -> list[tuple[str | None, str, list[str]]]:
    """Split numbered-format paragraphs into (None, bib_line, desc_paras) tuples."""
    books: list[tuple[str | None, str, list[str]]] = []
    current_bib: str | None = None
    current_desc: list[str] = []

    for para in paragraphs:
        if _is_numbered_bib(para):
            if current_bib is not None:
                books.append((None, current_bib, current_desc))
            current_bib = para
            current_desc = []
        elif current_bib is not None:
            current_desc.append(para)

    if current_bib is not None:
        books.append((None, current_bib, current_desc))

    return books


def _split_unnumbered(
    paragraphs: list[str],
) -> list[tuple[str | None, str, list[str]]]:
    """Split unnumbered paragraphs into (author|None, bib_line, desc_paras) tuples."""
    books: list[tuple[str | None, str, list[str]]] = []
    pending_author: str | None = None
    current_author: str | None = None
    current_bib: str | None = None
    current_desc: list[str] = []

    def _flush() -> None:
        nonlocal current_author, current_bib, current_desc
        if current_bib is not None:
            books.append((current_author, current_bib, current_desc))
        current_author = None
        current_bib = None
        current_desc = []

    for para in paragraphs:
        if _is_author_only_line(para):
            _flush()
            pending_author = _clean_author(para)
        elif _is_bib_line(para):
            if current_bib is not None:
                _flush()
            current_author = pending_author
            pending_author = None
            current_bib = para
        elif current_bib is not None:
            current_desc.append(para)

    _flush()
    return books


def _split_into_books(
    paragraphs: list[str],
) -> list[tuple[str | None, str, list[str]]]:
    """Dispatch to numbered or unnumbered splitting based on document format."""
    if any(_is_numbered_bib(p) for p in paragraphs):
        return _split_numbered(paragraphs)
    return _split_unnumbered(paragraphs)


# ---------------------------------------------------------------------------
# Interactive prompts
# ---------------------------------------------------------------------------


def _prompt_book(entry: ParsedBookEntry, index: int) -> ParsedBookEntry:
    typer.echo(f"\n--- Book {index} ---")
    typer.echo(f"  Bib: {entry.bib.full_text}")
    title = typer.prompt("  Title (element name)", default=entry.bib.title)
    author = typer.prompt("  Author", default=entry.bib.author)
    city = typer.prompt("  City", default=entry.bib.city)
    publisher = typer.prompt("  Publisher", default=entry.bib.publisher)
    year = typer.prompt("  Year", default=entry.bib.year)
    confirmed_bib = entry.bib.model_copy(
        update={
            "title": title,
            "author": author,
            "city": city,
            "publisher": publisher,
            "year": year,
        }
    )
    return entry.model_copy(update={"bib": confirmed_bib})


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def parse_books_file(path: str) -> list[ParsedBookEntry]:
    """Parse a .doc/.docx file containing one or more book entries.

    Supports two formats:
    - Numbered: '1. Author. Title / ...' all on one bib line per book
    - Unnumbered: optional standalone author line, then bib line, then description

    .doc files are auto-converted via macOS textutil before parsing.
    Displays interactive prompts for each book's bibliographic fields.
    Returns confirmed ParsedBookEntry objects (without images).
    """
    import shutil

    tmp_dir = None
    if os.path.splitext(path)[1].lower() == ".doc":
        converted = _convert_doc_to_docx(path)
        tmp_dir = os.path.dirname(converted)
        path = converted

    try:
        with zipfile.ZipFile(path) as zf:
            with zf.open("word/document.xml") as f:
                tree = ET.parse(f)
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    paragraphs = _get_paragraphs(tree)
    if not paragraphs:
        raise ValueError(f"Empty document: {path}")

    raw_books = _split_into_books(paragraphs)
    if not raw_books:
        raise ValueError(f"No book entries found in: {path}")

    entries: list[ParsedBookEntry] = []
    for i, (author, bib_line, desc_paras) in enumerate(raw_books, start=1):
        if author is not None:
            bib = _parse_bib_fields(bib_line, author=author)
        else:
            bib = _parse_numbered_bib(bib_line)
        description = _paragraphs_to_html(desc_paras)
        preview_text = f"<p>{_first_sentence(desc_paras[0])}</p>" if desc_paras else ""
        entry = ParsedBookEntry(
            bib=bib,
            description=description,
            preview_text=preview_text,
            sort=i * 10,
        )
        confirmed = _prompt_book(entry, i)
        entries.append(confirmed)

    return entries
