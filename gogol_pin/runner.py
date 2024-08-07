"""Script run."""

from gogol_pin.clients import DatabaseClient
from gogol_pin.service import PinService


async def run(
    database_uri: str,
    event_url: str,
    dry_run: bool,
) -> None:
    """Run the script."""
    database_client = DatabaseClient(database_uri, dry_run)
    pin_service = PinService(database_client)

    event = await pin_service.get_event(event_url)
    await pin_service.pin_event(event)
