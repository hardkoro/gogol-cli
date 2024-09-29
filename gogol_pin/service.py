"""Pin service."""

from datetime import datetime
import logging
import re
from gogol_pin.clients import DatabaseClient
from gogol_pin.schemas import Event
from gogol_pin.exceptions import GogolPinException


LOGGER = logging.getLogger(__name__)
DATE_FORMAT = "%Y-%m-%d"


class GogolService:
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

    async def export(self, month_number: int, year_suffix: str) -> list[dict[str, int]]:
        """Export monthly statistics."""
        LOGGER.info("Exporting monthly statistics for %s/%s ...", month_number, year_suffix)

        start_date, end_date = self._get_start_and_end_dates(month_number, year_suffix)

        statistics = await self._database_client.export_statistics(start_date, end_date)

        LOGGER.info("Finished exporting monthly statistics for %s/%s", month_number, year_suffix)

        return statistics

    @staticmethod
    def _get_start_and_end_dates(month_number: int, year_suffix: str) -> tuple[datetime, datetime]:
        """Get start and end dates for monthly statistics."""
        full_year = f"20{year_suffix}"

        # Start date is the first day of the given month
        start_date = datetime.strptime(f"{full_year}-{month_number}-01", DATE_FORMAT)

        # Calculate the first day of the next month
        if month_number == 12:  # If December, next month is January of the next year
            next_month_start_date = datetime.strptime(f"{int(full_year) + 1}-01-01", DATE_FORMAT)
        else:
            next_month_start_date = datetime.strptime(
                f"{full_year}-{month_number + 1}-01", DATE_FORMAT
            )

        return start_date, next_month_start_date
