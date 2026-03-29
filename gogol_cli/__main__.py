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
from gogol_cli.ssh_file_manager import SSHConfig
from gogol_cli.exporters.smtp import SMTPConfig, EmailConfig


load_dotenv()

app = typer.Typer()


@app.command()
def pin(
    database_uri: Annotated[str, typer.Option(help="Database URI", envvar="DATABASE_URI")],
    event_urls: Annotated[list[str], typer.Argument(help="Event URLs")],
    ssh_host: Annotated[str, typer.Option(help="SSH host", envvar="SSH_HOST")],
    ssh_username: Annotated[str, typer.Option(help="SSH username", envvar="SSH_USERNAME")],
    ssh_key_path: Annotated[str, typer.Option(help="SSH key path", envvar="SSH_KEY_PATH")],
    ssh_base_path: Annotated[str, typer.Option(help="SSH base path", envvar="SSH_BASE_PATH")],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Dry run")] = False,
) -> None:
    """Pin the event(s)."""
    uvloop.install()
    ssh_config = SSHConfig(
        host=ssh_host, username=ssh_username, key_path=ssh_key_path, base_path=ssh_base_path
    )
    asyncio.run(run_pin_event(database_uri, event_urls, dry_run, ssh_config))


@app.command()
def copy(
    database_uri: Annotated[str, typer.Option(help="Database URI", envvar="DATABASE_URI")],
    event_url: Annotated[str, typer.Argument(help="Event URL")],
    new_event_date_str: Annotated[str, typer.Argument(help="New event date (2024-10-20)")],
    new_event_time_str: Annotated[str, typer.Argument(help="New event time (18-00)")],
    ssh_host: Annotated[str, typer.Option(help="SSH host", envvar="SSH_HOST")],
    ssh_username: Annotated[str, typer.Option(help="SSH username", envvar="SSH_USERNAME")],
    ssh_key_path: Annotated[str, typer.Option(help="SSH key path", envvar="SSH_KEY_PATH")],
    ssh_base_path: Annotated[str, typer.Option(help="SSH base path", envvar="SSH_BASE_PATH")],
    new_price: Annotated[str, typer.Option(help="New event price (100–300)")] | None = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Dry run")] = False,
) -> None:
    """Copy the event."""
    uvloop.install()
    ssh_config = SSHConfig(
        host=ssh_host, username=ssh_username, key_path=ssh_key_path, base_path=ssh_base_path
    )
    asyncio.run(
        run_copy_event(
            database_uri,
            event_url,
            new_event_date_str,
            new_event_time_str,
            new_price,
            dry_run,
            ssh_config,
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
    smtp_config = SMTPConfig(
        host=smtp_host, port=smtp_port, username=smtp_username, password=smtp_password
    )
    email_config = EmailConfig(
        from_addr=from_addr,
        to_addr=to_addr,
        subject=f"Отчёт об удалённой работе за {str(month_number).zfill(2)}.20{year_suffix}",
    )
    asyncio.run(
        run_export(database_uri, month_number, year_suffix, dry_run, smtp_config, email_config)
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
