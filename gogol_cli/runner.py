"""Script run."""

import os
import re
from datetime import date, datetime
from pathlib import Path

import typer

from gogol_cli.clients import DatabaseClient
from gogol_cli.events.docx_parser import _prompt_event_details, parse_event_file
from gogol_cli.exhibition.docx_parser import parse_exhibition_folder
from gogol_cli.exceptions import EmailConfigError, SMTPConfigError
from gogol_cli.exporters import AbstractExporter, PlainExporter, SMTPExporter
from gogol_cli.exporters.smtp import EmailConfig, SMTPConfig
from gogol_cli.service import GogolCLIService
from gogol_cli.ssh_file_manager import SSHConfig, SSHFileManager
from gogol_cli.virtual_exhibition.parser import parse_virtual_exhibition_folder


async def pin_event(
    database_uri: str,
    event_urls: list[str],
    dry_run: bool,
    ssh_config: SSHConfig,
) -> None:
    """Run the script."""
    database_client = DatabaseClient(database_uri)
    ssh_file_manager = SSHFileManager(ssh_config)
    cli_service = GogolCLIService(database_client, ssh_file_manager, dry_run)

    for event_url in event_urls:
        event = await cli_service.get_event(event_url)
        await cli_service.pin_event(event)


async def copy_event(
    database_uri: str,
    event_url: str,
    new_event_date_str: str,
    new_event_time_str: str,
    new_price: str | None,
    dry_run: bool,
    ssh_config: SSHConfig,
) -> None:
    """Run the script."""
    database_client = DatabaseClient(database_uri)
    ssh_file_manager = SSHFileManager(ssh_config)
    cli_service = GogolCLIService(database_client, ssh_file_manager, dry_run)

    old_event = await cli_service.get_event(event_url)
    await cli_service.copy_event(old_event, new_event_date_str, new_event_time_str, new_price)


async def xcopy_events(
    database_uri: str,
    instructions: list[tuple[str, list[tuple[date, str]]]],
    dry_run: bool,
    ssh_config: SSHConfig,
) -> None:
    """Copy one or more events from parsed xcopy instructions."""
    database_client = DatabaseClient(database_uri)
    ssh_file_manager = SSHFileManager(ssh_config)
    cli_service = GogolCLIService(database_client, ssh_file_manager, dry_run)

    for url, date_times in instructions:
        old_event = await cli_service.get_event(url)
        for d, time_str in date_times:
            await cli_service.copy_event(old_event, d.strftime("%Y-%m-%d"), time_str, None)


async def export_statistics(
    database_uri: str,
    month_number: int,
    year_suffix: str,
    dry_run: bool,
    smtp_config: SMTPConfig | None = None,
    email_config: EmailConfig | None = None,
) -> None:
    """Run the script."""
    database_client = DatabaseClient(database_uri)
    cli_service = GogolCLIService(database_client, dry_run=dry_run)

    statistics = await cli_service.export(month_number, year_suffix)

    if dry_run:
        exporter: AbstractExporter = PlainExporter()
    else:
        if smtp_config is None:
            raise SMTPConfigError("SMTP config is not provided")
        if email_config is None:
            raise EmailConfigError("Email config is not provided")
        exporter = SMTPExporter(smtp_config, email_config)

    exporter.export(statistics)


async def copy_chronograph(
    database_uri: str,
    month_number: int,
    year_suffix: str,
    dry_run: bool,
) -> None:
    """Run the script."""
    database_client = DatabaseClient(database_uri)
    cli_service = GogolCLIService(database_client, dry_run=dry_run)

    await cli_service.copy_chronograph(month_number, year_suffix)


async def create_exhibition(
    database_uri: str,
    folder_path: str,
    active_from: datetime,
    dry_run: bool,
    ssh_config: SSHConfig,
) -> None:
    """Run the exhibition creation script."""
    parsed = parse_exhibition_folder(folder_path)

    database_client = DatabaseClient(database_uri)
    ssh_file_manager = SSHFileManager(ssh_config)
    cli_service = GogolCLIService(database_client, ssh_file_manager, dry_run)

    await cli_service.create_exhibition(parsed, active_from)


async def create_virtual_exhibition(
    database_uri: str,
    folder_path: str,
    dry_run: bool,
    ssh_config: SSHConfig,
) -> None:
    """Run the virtual exhibition creation script."""
    parsed = parse_virtual_exhibition_folder(folder_path)

    database_client = DatabaseClient(database_uri)
    ssh_file_manager = SSHFileManager(ssh_config)
    cli_service = GogolCLIService(database_client, ssh_file_manager, dry_run)

    await cli_service.create_virtual_exhibition(parsed)


def _collect_docx_files(folder_path: str) -> list[str]:
    return sorted(
        f for f in os.listdir(folder_path) if f.endswith(".docx") and not f.startswith("~$")
    )


def _collect_image_files(folder_path: str) -> dict[str, str]:
    return {
        f: os.path.join(folder_path, f)
        for f in os.listdir(folder_path)
        if f.lower().endswith((".png", ".jpg", ".jpeg", ".gif"))
    }


def _pick_manual_image(image_files: dict[str, str]) -> tuple[bytes | None, str | None]:
    if not image_files:
        return None, None

    typer.echo("\nAvailable images:")
    sorted_names = sorted(image_files.keys())
    for idx, img_name in enumerate(sorted_names, 1):
        typer.echo(f"  {idx}. {img_name}")

    choice = typer.prompt("Select image (number or skip with 'n')", default="n")
    if choice.lower() == "n" or not choice.isdigit():
        return None, None

    selected_idx = int(choice) - 1
    if selected_idx < 0 or selected_idx >= len(sorted_names):
        return None, None

    selected = sorted_names[selected_idx]
    with open(image_files[selected], "rb") as f:
        return f.read(), selected


def _pick_image_for_event(
    event_date: datetime,
    image_files: dict[str, str],
) -> tuple[bytes | None, str | None]:
    event_date_str = event_date.strftime("%d.%m")
    matched_image: str | None = None

    for img_file in image_files:
        if re.search(r"\d{2}\.\d{2}", img_file) and event_date_str.replace(
            ".", ""
        ) in img_file.replace(".", ""):
            matched_image = img_file
            break

    if matched_image is not None:
        typer.echo(f"Auto-matched image: {matched_image}")
        if typer.confirm("Use this image?", default=True):
            with open(image_files[matched_image], "rb") as f:
                return f.read(), matched_image

    return _pick_manual_image(image_files)


def _load_default_event_image() -> tuple[bytes | None, str | None]:
    default_image_filename = "рояль.jpg"
    default_image_path = Path(__file__).resolve().parent / "resources" / default_image_filename

    if not default_image_path.exists():
        return None, None

    with default_image_path.open("rb") as f:
        return f.read(), default_image_filename


async def _process_single_event(
    cli_service: GogolCLIService,
    docx_path: str,
    docx_file: str,
    image_files: dict[str, str],
) -> None:
    typer.echo(f"\n{'=' * 60}")
    typer.echo(f"Processing: {docx_file}")
    typer.echo(f"{'=' * 60}")

    parsed_event = parse_event_file(docx_path)
    confirmed_event = _prompt_event_details(parsed_event)

    if confirmed_event.image_data:
        matched_image_data = confirmed_event.image_data
        matched_image_filename = confirmed_event.image_filename
    else:
        matched_image_data, matched_image_filename = _pick_image_for_event(
            confirmed_event.date_time,
            image_files,
        )
        if matched_image_data is None or matched_image_filename is None:
            matched_image_data, matched_image_filename = _load_default_event_image()
            if matched_image_data is not None and matched_image_filename is not None:
                typer.echo(f"Using default image: {matched_image_filename}")

    await cli_service.add_event(
        name=confirmed_event.name,
        event_date_time=confirmed_event.date_time,
        description_html=confirmed_event.description,
        price=confirmed_event.price,
        purchase_link=confirmed_event.purchase_link,
        registration_link=confirmed_event.registration_link,
        tags=confirmed_event.tags,
        image_data=matched_image_data,
        image_filename=matched_image_filename,
    )

    typer.echo("✓ Event created successfully")


async def add_events(
    database_uri: str,
    folder_path: str,
    dry_run: bool,
    ssh_config: SSHConfig,
) -> None:
    """Run the event creation script with improved structure."""

    database_client = DatabaseClient(database_uri)
    ssh_file_manager = SSHFileManager(ssh_config)
    cli_service = GogolCLIService(database_client, ssh_file_manager, dry_run)

    all_docx = _collect_docx_files(folder_path)

    if not all_docx:
        typer.echo(f"No .docx files found in {folder_path}")
        return

    image_files = _collect_image_files(folder_path)

    for docx_file in sorted(all_docx):
        docx_path = os.path.join(folder_path, docx_file)
        await _process_single_event(
            cli_service=cli_service,
            docx_path=docx_path,
            docx_file=docx_file,
            image_files=image_files,
        )
