"""CLI entrypoint."""

import asyncio
import logging

from typing import Annotated

import typer
import uvloop
from dotenv import load_dotenv

from gogol_cli.runner import pin_event as run_pin_event
from gogol_cli.runner import copy_event as run_copy_event
from gogol_cli.runner import export_statistics as run_export
from gogol_cli.runner import copy_chronograph as run_chronograph

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
    new_event_date_str: Annotated[str, typer.Argument(help="New event date (2024-10-20)")],
    new_event_time_str: Annotated[str, typer.Argument(help="New event time (18-00)")],
    new_price: Annotated[str, typer.Option(help="New event price (100–300)")] | None = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Dry run")] = False,
) -> None:
    """Copy the event."""
    uvloop.install()
    asyncio.run(
        run_copy_event(
            database_uri,
            event_url,
            new_event_date_str,
            new_event_time_str,
            new_price,
            dry_run,
        )
    )


@app.command()
def export(
    database_uri: Annotated[str, typer.Option(help="Database URI", envvar="DATABASE_URI")],
    month_number: Annotated[int, typer.Argument(help="Month number (1-12)")],
    year_suffix: Annotated[
        str, typer.Argument(help="Two last letters of the year (24, 25, and so on)")
    ],
    smtp_host: Annotated[str, typer.Option(help="SMTP host", envvar="SMTP_HOST")],
    smtp_port: Annotated[int, typer.Option(help="SMTP port", envvar="SMTP_PORT")],
    smtp_username: Annotated[str, typer.Option(help="SMTP username", envvar="SMTP_USERNAME")],
    smtp_password: Annotated[str, typer.Option(help="SMTP password", envvar="SMTP_PASSWORD")],
    from_addr: Annotated[str, typer.Option(help="From address", envvar="FROM_ADDR")],
    to_addr: Annotated[str, typer.Option(help="To address", envvar="TO_ADDR")],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Dry run")] = False,
) -> None:
    """Export monthly statistics."""
    uvloop.install()

    asyncio.run(
        run_export(
            database_uri,
            month_number,
            year_suffix,
            dry_run,
            smtp_host,
            smtp_port,
            smtp_username,
            smtp_password,
            from_addr,
            to_addr,
        )
    )


@app.command()
def chrono(
    database_uri: Annotated[str, typer.Option(help="Database URI", envvar="DATABASE_URI")],
    month_number: Annotated[int, typer.Argument(help="Month number (1-12)")],
    year_suffix: Annotated[
        str, typer.Argument(help="Two last letters of the year (24, 25, and so on)")
    ],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Dry run")] = False,
) -> None:
    """Run the chronograph."""
    uvloop.install()
    asyncio.run(run_chronograph(database_uri, month_number, year_suffix, dry_run))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app()
