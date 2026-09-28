"""Parse a single news entry from a .docx file."""

from __future__ import annotations

import zipfile
from xml.etree import ElementTree as ET

import typer

from gogol_cli.exhibition.docx_parser import _collapse_spaces, _get_paragraphs, _paragraphs_to_html
from gogol_cli.news.schemas import ParsedNews


def _prompt_title(title: str) -> str:
    typer.echo(f"\nParsed news title: {title}")
    return typer.prompt("News title", default=title)


def parse_news_file(path: str) -> ParsedNews:
    """Parse a .docx file into a ParsedNews.

    Structure detected:
    - Para 0: title (becomes the element name)
    - Remaining paras: body <p> elements; the first one doubles as the preview text.

    Displays an interactive prompt so the user can confirm or correct the title.
    """
    with zipfile.ZipFile(path) as zf:
        with zf.open("word/document.xml") as f:
            tree = ET.parse(f)

    paragraphs = _get_paragraphs(tree)
    if not paragraphs:
        raise ValueError(f"Empty document: {path}")

    title = _collapse_spaces(paragraphs[0])
    title = _prompt_title(title)

    body = paragraphs[1:]
    detail_text = _paragraphs_to_html(body)
    preview_text = f"<p>{body[0]}</p>" if body else ""

    return ParsedNews(title=title, detail_text=detail_text, preview_text=preview_text)
