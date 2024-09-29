"""CLI entrypoint."""

import asyncio
import logging

from datetime import datetime
from typing import Annotated

import typer
import uvloop
from dotenv import load_dotenv

from gogol_pin.runner import pin_event as run_pin_event
from gogol_pin.runner import copy_event as run_copy_event
from gogol_pin.runner import export_statistics as run_export

load_dotenv()

app = typer.Typer()


@app.command()
def pin(
    database_uri: Annotated[str, typer.Option(help="Database URI", envvar="DATABASE_URI")],
    event_url: Annotated[str, typer.Argument(help="Event URL")],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Dry run")] = False,
) -> None:
    """Pin the event."""
    uvloop.install()
    asyncio.run(run_pin_event(database_uri, event_url, dry_run))


@app.command()
def copy(
    database_uri: Annotated[str, typer.Option(help="Database URI", envvar="DATABASE_URI")],
    event_url: Annotated[str, typer.Argument(help="Event URL")],
    new_event_datetime: Annotated[datetime, typer.Option(help="New event date and time")],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Dry run")] = False,
) -> None:
    """Copy the event."""
    uvloop.install()
    asyncio.run(run_copy_event(database_uri, event_url, dry_run, new_event_datetime))


@app.command()
def export(
    database_uri: Annotated[str, typer.Option(help="Database URI", envvar="DATABASE_URI")],
    month_number: Annotated[int, typer.Argument(help="Month number (1-12)")],
    year_suffix: Annotated[
        str, typer.Argument(help="Two last letters of the year (24, 25, and so on)")
    ],
) -> None:
    """Export monthly statistics."""
    uvloop.install()
    asyncio.run(run_export(database_uri, month_number, year_suffix))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app()
