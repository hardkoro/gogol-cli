"""Script run."""

from gogol_cli.clients import DatabaseClient
from gogol_cli.exceptions import SMTPConfigError, EmailConfigError
from gogol_cli.exporters import (
    AbstractExporter,
    EmailConfig,
    SMTPConfig,
    SMTPExporter,
    PlainExporter,
)
from gogol_cli.service import GogolCLIService


async def pin_event(
    database_uri: str,
    event_url: str,
    dry_run: bool,
) -> None:
    """Run the script."""
    database_client = DatabaseClient(database_uri, dry_run)
    cli_service = GogolCLIService(database_client)

    event = await cli_service.get_event(event_url)
    await cli_service.pin_event(event)


async def copy_event(
    database_uri: str,
    event_url: str,
    new_event_date_str: str,
    new_event_time_str: str,
    new_price: str | None,
    dry_run: bool,
) -> None:
    """Run the script."""
    database_client = DatabaseClient(database_uri, dry_run)
    cli_service = GogolCLIService(database_client)

    old_event = await cli_service.get_event(event_url)
    await cli_service.copy_event(
        old_event,
        new_event_date_str,
        new_event_time_str,
        new_price,
    )


async def export_statistics(  # pylint: disable=too-many-locals
    database_uri: str,
    month_number: int,
    year_suffix: str,
    dry_run: bool,
    smtp_host: str | None = None,
    smtp_port: int | None = None,
    smtp_username: str | None = None,
    smtp_password: str | None = None,
    from_addr: str | None = None,
    to_addr: str | None = None,
) -> None:
    """Run the script."""
    database_client = DatabaseClient(database_uri, dry_run=False)
    cli_service = GogolCLIService(database_client)

    statistics = await cli_service.export(month_number, year_suffix)

    if dry_run:
        exporter: AbstractExporter = PlainExporter()
    else:
        smtp_config = SMTPConfig(
            host=smtp_host,
            port=smtp_port,
            username=smtp_username,
            password=smtp_password,
        )
        if not smtp_config.is_valid:
            raise SMTPConfigError("SMTP config is not valid")

        email_config = EmailConfig(
            from_addr=from_addr,
            to_addr=to_addr,
            subject=f"Отчёт об удалённой работе за {str(month_number).zfill(2)}.20{year_suffix}",
        )
        if not email_config.is_valid:
            raise EmailConfigError("Email config is not valid")

        exporter = SMTPExporter(smtp_config, email_config)

    exporter.export(statistics)


async def copy_chronograph(
    database_uri: str,
    month_number: int,
    year_suffix: str,
    dry_run: bool,
) -> None:
    """Run the script."""
    database_client = DatabaseClient(database_uri, dry_run=dry_run)
    cli_service = GogolCLIService(database_client)

    await cli_service.copy_chronograph(month_number, year_suffix)
