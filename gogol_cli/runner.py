"""Script run."""

from datetime import datetime

from gogol_cli.clients import DatabaseClient
from gogol_cli.exceptions import EmailConfigError, SMTPConfigError
from gogol_cli.exporters import AbstractExporter, PlainExporter, SMTPExporter
from gogol_cli.exporters.smtp import EmailConfig, SMTPConfig
from gogol_cli.service import GogolCLIService
from gogol_cli.ssh_file_manager import SSHConfig, SSHFileManager


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
    from gogol_cli.exhibition.docx_parser import parse_exhibition_folder

    parsed = parse_exhibition_folder(folder_path)

    database_client = DatabaseClient(database_uri)
    ssh_file_manager = SSHFileManager(ssh_config)
    cli_service = GogolCLIService(database_client, ssh_file_manager, dry_run)

    await cli_service.create_exhibition(parsed, active_from)
