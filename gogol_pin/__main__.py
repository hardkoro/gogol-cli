"""CLI entrypoint."""

import asyncio
import logging

import click
import uvloop
from dotenv import load_dotenv

from gogol_pin.runner import run as run_pinner

load_dotenv()

@click.command(name="pin")
@click.option("--database_uri", envvar="DATABASE_URI", required=True, help="Database URI", type=str)
@click.option("--event_url", required=True, help="Event URL", type=str)
def pin(database_uri: str, event_url: str) -> None:
    """Start the script."""
    uvloop.install()
    asyncio.run(run_pinner(database_uri, event_url))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    pin()
