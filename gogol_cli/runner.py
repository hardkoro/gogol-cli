"""Script run."""

from gogol_cli.clients import DatabaseClient
from gogol_cli.exporter import PlainExporter
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
    new_event_time_str: str | None,
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


async def export_statistics(
    database_uri: str,
    month_number: int,
    year_suffix: str,
) -> None:
    """Run the script."""
    database_client = DatabaseClient(database_uri, dry_run=False)
    cli_service = GogolCLIService(database_client)

    statistics = await cli_service.export(month_number, year_suffix)

    exporter = PlainExporter()
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
