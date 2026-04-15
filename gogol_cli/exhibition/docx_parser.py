"""Parse exhibition data from a folder of .docx files."""

import os
import re
import zipfile
from xml.etree import ElementTree as ET

import typer

from gogol_cli.exhibition.schemas import BibInfo, ParsedBook, ParsedExhibition

_WNS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

# Quote pairs (open, close) that may wrap exhibition titles.
# «» must be first so it is detected before being re-wrapped.
_QUOTE_PAIRS = [
    ("\u00ab", "\u00bb"),  # «»
    ("\u201c", "\u201d"),  # ""
    ("\u201e", "\u201c"),  # „"
    ("\u2018", "\u2019"),  # ''
    ('"', '"'),
    ("'", "'"),
]


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _collapse_spaces(text: str) -> str:
    """Replace non-breaking spaces, then collapse runs of spaces to one."""
    text = text.replace("\xa0", " ")
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def _sentence_case(text: str) -> str:
    """Lowercase everything then capitalise the first alphabetic character."""
    lowered = text.lower()
    for i, ch in enumerate(lowered):
        if ch.isalpha():
            return lowered[:i] + ch.upper() + lowered[i + 1 :]
    return lowered


def _normalize_title(raw: str) -> str:
    """Normalise exhibition title: strip wrapping quotes, apply sentence case
    if ALL CAPS, then always wrap the result in «»."""
    text = raw.strip()

    inner = text
    for open_q, close_q in _QUOTE_PAIRS:
        if inner.startswith(open_q) and inner.endswith(close_q) and len(inner) > len(open_q):
            inner = inner[len(open_q) : -len(close_q)]
            break

    if any(c.isalpha() for c in inner) and inner == inner.upper():
        inner = _sentence_case(inner)

    return f"\u00ab{inner}\u00bb"


def _paragraphs_to_html(paragraphs: list[str]) -> str:
    return "\n".join(f"<p>{p}</p>" for p in paragraphs if p)


def _first_sentence(text: str) -> str:
    """Return text up to and including the first sentence-ending punctuation."""
    match = re.search(r"[.!?]", text)
    if match:
        return text[: match.start() + 1]
    return text


# ---------------------------------------------------------------------------
# docx helpers
# ---------------------------------------------------------------------------


def _get_paragraphs(tree: ET.ElementTree[ET.Element]) -> list[str]:
    """Extract collapsed, stripped, non-empty paragraph texts."""
    ns = {"w": _WNS}
    result = []
    for p in tree.findall(".//w:p", ns):
        runs = p.findall(".//w:t", ns)
        text = "".join(r.text or "" for r in runs)
        text = _collapse_spaces(text)
        if text:
            result.append(text)
    return result


def _extract_image(zf: zipfile.ZipFile) -> tuple[bytes, str] | None:
    """Return (bytes, filename) for the first image found in a docx zip."""
    for name in zf.namelist():
        if name.startswith("word/media/"):
            return zf.read(name), os.path.basename(name)
    return None


def _is_author_line(text: str) -> bool:
    """Heuristic: 'Lastname, Firstname [Middlename]' — no publication markers."""
    if ". -" in text or " / " in text or " : " in text:
        return False
    return bool(re.match(r"^\w[\w\s]*,\s+\w", text))


# ---------------------------------------------------------------------------
# Bib parsing
# ---------------------------------------------------------------------------


def _parse_bib(author_line: str | None, bib_line: str) -> BibInfo:
    bib = bib_line.strip()

    # Year: first 4-digit number in plausible range
    year_match = re.search(r"\b(1[89]\d{2}|20\d{2})\b", bib)
    year = year_match.group(1) if year_match else ""

    # City and publisher: `. - CITY : PUBLISHER`
    city_pub_match = re.search(
        r"\.\s*-\s*([^:\-\n]+?)\s*:\s*([^,;\-\n]+?)(?:,\s*\d{4}|[.;]|-\s*\d)",
        bib,
    )
    city = city_pub_match.group(1).strip() if city_pub_match else ""
    publisher = city_pub_match.group(2).strip() if city_pub_match else ""

    # Title: everything before first ` / ` or `. - `
    title_match = re.match(r"^(.+?)(?:\s+/\s+|\.\s+-\s)", bib)
    title = title_match.group(1).strip() if title_match else bib.split(". ")[0].strip()

    author = author_line.strip() if author_line else ""

    return BibInfo(
        title=title,
        author=author,
        city=city,
        publisher=publisher,
        year=year,
        full_text=bib,
    )


# ---------------------------------------------------------------------------
# Interactive prompts
# ---------------------------------------------------------------------------


def _prompt_title(title: str) -> str:
    typer.echo(f"\nParsed exhibition title: {title}")
    return typer.prompt("Exhibition title", default=title)


def _prompt_bib(bib: BibInfo, book_index: int) -> BibInfo:
    typer.echo(f"\n--- Book {book_index} ---")
    typer.echo(f"  Bib line: {bib.full_text[:100]}")
    title = typer.prompt("  Title (element name)", default=bib.title)
    author = typer.prompt("  Author (prop 31)", default=bib.author)
    city = typer.prompt("  City abbreviation (prop 57)", default=bib.city)
    publisher = typer.prompt("  Publisher (prop 58)", default=bib.publisher)
    year = typer.prompt("  Year (prop 59)", default=bib.year)
    return bib.model_copy(
        update={
            "title": title,
            "author": author,
            "city": city,
            "publisher": publisher,
            "year": year,
        }
    )


# ---------------------------------------------------------------------------
# Per-file parsers
# ---------------------------------------------------------------------------


def _extract_quote_block(rest: list[str], start_idx: int) -> tuple[str | None, int]:
    """Scan paragraphs from start_idx for a quote block.

    Returns (blockquote_html | None, next_idx).
    """
    idx = start_idx
    quote_lines: list[str] = []
    in_quote = False
    attribution: str | None = None

    while idx < len(rest):
        para = rest[idx]
        has_open = "\u00ab" in para
        has_close = "\u00bb" in para

        if not in_quote:
            if has_open:
                in_quote = True
                quote_lines.append(para)
                idx += 1
                if has_close:
                    if idx < len(rest):
                        attribution = rest[idx]
                        idx += 1
                    break
            else:
                break
        else:
            quote_lines.append(para)
            idx += 1
            if has_close:
                if idx < len(rest):
                    attribution = rest[idx]
                    idx += 1
                break

    if not quote_lines:
        return None, start_idx

    quote_text = " ".join(quote_lines)
    quote_text = re.sub(r"\u00bb[.!?,;]*$", "\u00bb", quote_text)
    attr_line = f"\t {attribution}" if attribution else ""
    html = (
        f'<blockquote style="text-align:right">\n'
        f"\t {quote_text} <br>\n"
        f" <br>\n"
        f"{attr_line}\n"
        f"</blockquote>"
    )
    return html, idx


def _parse_title_file(path: str) -> tuple[str, str, str]:
    """Parse the first docx into (title, detail_text_html, preview_text_html).

    Structure detected:
    - Para 0: title (becomes element name)
    - Para 1 (optional): sub-header wrapped in parentheses → <h2>
    - Next paras: optional quote block wrapped in <blockquote>
    - Remaining paras: body <p> elements.
    """
    with zipfile.ZipFile(path) as zf:
        with zf.open("word/document.xml") as f:
            tree = ET.parse(f)
    paragraphs = _get_paragraphs(tree)
    if not paragraphs:
        raise ValueError(f"Empty title file: {path}")

    title = _normalize_title(paragraphs[0])
    rest = paragraphs[1:]
    idx = 0
    html_parts: list[str] = []

    if rest and rest[0].startswith("(") and rest[0].endswith(")"):
        html_parts.append(f"<h2>{rest[0][1:-1].strip()}</h2>")
        idx = 1

    blockquote, idx = _extract_quote_block(rest, idx)
    if blockquote:
        html_parts.append(blockquote)

    body = rest[idx:]
    html_parts.extend(f"<p>{p}</p>" for p in body if p)

    detail_text = "\n".join(html_parts)
    preview_text = f"<p>{body[0]}</p>" if body else ""
    return title, detail_text, preview_text


def _parse_illustration_file(path: str) -> tuple[bytes, str]:
    """Return (image_bytes, filename) from the illustration docx."""
    with zipfile.ZipFile(path) as zf:
        result = _extract_image(zf)
    if result is None:
        raise ValueError(f"No image found in illustration file: {path}")
    return result


def _parse_book_file(path: str, sort: int) -> ParsedBook:
    with zipfile.ZipFile(path) as zf:
        img_result = _extract_image(zf)
        with zf.open("word/document.xml") as f:
            tree = ET.parse(f)

    if img_result is None:
        raise ValueError(f"No image found in book file: {path}")
    cover_data, cover_filename = img_result

    paragraphs = _get_paragraphs(tree)

    if len(paragraphs) > 1 and _is_author_line(paragraphs[0]):
        author_line: str | None = paragraphs[0]
        bib_line = paragraphs[1]
        desc_paragraphs = paragraphs[2:]
    else:
        author_line = None
        bib_line = paragraphs[0] if paragraphs else ""
        desc_paragraphs = paragraphs[1:]

    bib = _parse_bib(author_line, bib_line)
    description = _paragraphs_to_html(desc_paragraphs)
    preview_text = f"<p>{_first_sentence(desc_paragraphs[0])}</p>" if desc_paragraphs else ""

    return ParsedBook(
        cover_data=cover_data,
        cover_filename=cover_filename,
        bib=bib,
        description=description,
        preview_text=preview_text,
        sort=sort,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def parse_exhibition_folder(folder_path: str) -> ParsedExhibition:
    """Parse a folder of .docx files into a ParsedExhibition.

    Displays interactive prompts so the user can confirm or correct the
    parsed exhibition title and per-book bibliographic fields.
    """
    all_docx = [f for f in os.listdir(folder_path) if f.endswith(".docx")]

    numbered = sorted(
        (f for f in all_docx if f[0].isdigit()),
        key=lambda f: int(m.group(1)) if (m := re.match(r"^(\d+)", f)) else 0,
    )
    unnumbered = [f for f in all_docx if not f[0].isdigit()]

    if not numbered:
        raise ValueError(f"No numbered .docx files found in: {folder_path}")
    if not unnumbered:
        raise ValueError("No unnumbered illustration .docx file found.")

    title_file = numbered[0]
    book_files = numbered[1:]
    illustration_file = unnumbered[0]

    # --- Title & description ---
    title, detail_text, preview_text = _parse_title_file(os.path.join(folder_path, title_file))
    title = _prompt_title(title)

    # --- Illustration ---
    illus_data, illus_filename = _parse_illustration_file(
        os.path.join(folder_path, illustration_file)
    )

    # --- Books ---
    books: list[ParsedBook] = []
    for i, book_file in enumerate(book_files, start=1):
        book = _parse_book_file(os.path.join(folder_path, book_file), sort=i * 10)
        confirmed_bib = _prompt_bib(book.bib, i)
        books.append(book.model_copy(update={"bib": confirmed_bib}))

    return ParsedExhibition(
        title=title,
        detail_text=detail_text,
        preview_text=preview_text,
        illustration_data=illus_data,
        illustration_filename=illus_filename,
        books=books,
    )
