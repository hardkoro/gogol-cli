"""Pin service."""

from datetime import datetime
import logging
import re
from gogol_pin.clients import DatabaseClient
from gogol_pin.schemas import Event
from gogol_pin.exceptions import GogolPinException


LOGGER = logging.getLogger(__name__)


class PinService:
    """Pin service."""

    def __init__(self, database_client: DatabaseClient) -> None:
        """Initialize the service."""
        self._database_client = database_client

    async def get_event(self, event_url: str) -> Event:
        """Get event by URL."""
        LOGGER.info("Getting event from %s ...", event_url)

        event_id_match = re.search(r"/(\d+)/?$", event_url)
        if event_id_match is None:
            raise GogolPinException(f"Invalid event URL: {event_url}")
        event_id = event_id_match.group(1)

        event = await self._database_client.get_event_by_id(event_id)

        LOGGER.info("Finished getting event from %s", event_url)

        return event

    async def pin_event(self, event: Event) -> None:
        """Pin event."""
        LOGGER.info("Pinning event %s ...", event.id)

        await self._database_client.pin_event(event)

        LOGGER.info("Finished pinning event %s", event.id)

    async def copy_event(self, event: Event, new_event_datetime: datetime) -> None:
        """Copy event."""
        LOGGER.info("Copying event %s to %s ...", event.id, new_event_datetime)

        await self._database_client.copy_event(event, new_event_datetime)

        LOGGER.info("Finished copying event %s to %s", event.id, new_event_datetime)
