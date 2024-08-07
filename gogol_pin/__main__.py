"""CLI entrypoint."""

import asyncio
import logging

import typer
import uvloop
from dotenv import load_dotenv
from typing import Annotated

from gogol_pin.runner import run as run_pinner

load_dotenv()

app = typer.Typer()


@app.command()
def cli(
    database_uri: Annotated[str, typer.Option(help="Database URI", envvar="DATABASE_URI")],
    event_url: Annotated[str, typer.Option(help="Event URL")],
    dry_run: Annotated[bool, typer.Option(help="Dry run")],
) -> None:
    """Start the script."""
    uvloop.install()
    asyncio.run(run_pinner(database_uri, event_url, dry_run))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app()
