"""Script run."""

from datetime import datetime
from gogol_pin.clients import DatabaseClient
from gogol_pin.exporter import PlainExporter
from gogol_pin.service import GogolService


async def pin_event(
    database_uri: str,
    event_url: str,
    dry_run: bool,
) -> None:
    """Run the script."""
    database_client = DatabaseClient(database_uri, dry_run)
    pin_service = GogolService(database_client)

    event = await pin_service.get_event(event_url)
    await pin_service.pin_event(event)


async def copy_event(
    database_uri: str,
    event_url: str,
    dry_run: bool,
    new_event_datetime: datetime,
) -> None:
    """Run the script."""
    database_client = DatabaseClient(database_uri, dry_run)
    pin_service = GogolService(database_client)

    event = await pin_service.get_event(event_url)
    await pin_service.copy_event(event, new_event_datetime)


async def export_statistics(
    database_uri: str,
    month_number: int,
    year_suffix: str,
) -> None:
    """Run the script."""
    database_client = DatabaseClient(database_uri, dry_run=False)
    pin_service = GogolService(database_client)

    statistics = await pin_service.export(month_number, year_suffix)

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
    pin_service = GogolService(database_client)

    await pin_service.copy_chronograph(month_number, year_suffix)
