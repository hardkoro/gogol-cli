"""Pin service."""

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
        LOGGER.info(f"Getting event from {event_url} ...")

        event_id_match = re.search(r'/(\d+)/?$', event_url)
        if event_id_match is None:
            raise GogolPinException(f"Invalid event URL: {event_url}")
        event_id = event_id_match.group(1)

        event = await self._database_client.get_event_by_id(event_id)

        LOGGER.info(f"Finished getting event from {event_url}")

        return event

    async def pin_event(self, event: Event) -> None:
        """Pin event."""
        LOGGER.info(f"Pinning event {event.id} ...")

        await self._database_client.pin_event(event)

        LOGGER.info(f"Finished pinning event {event.id}")
