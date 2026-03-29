"""Script run."""

from gogol_cli.clients import DatabaseClient
from gogol_cli.exceptions import SMTPConfigError, EmailConfigError
from gogol_cli.exporters import (
    AbstractExporter,
    SMTPExporter,
    PlainExporter,
    EmailConfig,
    SMTPConfig,
)
from gogol_cli.service import GogolCLIService
from gogol_cli.ssh_file_manager import SSHConfig, SSHFileManager


async def pin_event(
    database_uri: str,
    event_urls: list[str],
    dry_run: bool,
    ssh_config: SSHConfig,
) -> None:
    """Run the script."""
    ssh_file_manager = SSHFileManager(ssh_config)
    database_client = DatabaseClient(ssh_file_manager, database_uri, dry_run)
    cli_service = GogolCLIService(database_client)

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
    ssh_file_manager = SSHFileManager(ssh_config)
    database_client = DatabaseClient(ssh_file_manager, database_uri, dry_run)
    cli_service = GogolCLIService(database_client)

    old_event = await cli_service.get_event(event_url)
    await cli_service.copy_event(
        old_event,
        new_event_date_str,
        new_event_time_str,
        new_price,
    )


async def export_statistics(
    database_uri: str,
    month_number: int,
    year_suffix: str,
    dry_run: bool,
    ssh_config: SSHConfig,
    smtp_config: SMTPConfig | None = None,
    email_config: EmailConfig | None = None,
) -> None:
    """Run the script."""
    ssh_file_manager = SSHFileManager(ssh_config)
    database_client = DatabaseClient(ssh_file_manager, database_uri, dry_run)
    cli_service = GogolCLIService(database_client)

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
    ssh_config: SSHConfig,
) -> None:
    """Run the script."""
    ssh_file_manager = SSHFileManager(ssh_config)
    database_client = DatabaseClient(ssh_file_manager, database_uri, dry_run)
    cli_service = GogolCLIService(database_client)

    await cli_service.copy_chronograph(month_number, year_suffix)
