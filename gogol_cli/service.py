"""Gogol CLI service."""

from datetime import datetime
import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from gogol_cli.clients import DatabaseClient
from gogol_cli import constants as const
from gogol_cli.schemas import Event
from gogol_cli.exceptions import GogolCLIException, SSHNotConfiguredError
from gogol_cli.ssh_file_manager import SSHFileManager


LOGGER = logging.getLogger(__name__)


class GogolCLIService:
    """Gogol CLI service."""

    def __init__(
        self,
        database_client: DatabaseClient,
        ssh_file_manager: SSHFileManager | None = None,
        dry_run: bool = False,
    ) -> None:
        """Initialize the service.

        Args:
            database_client: The instance of the database client.
            ssh_file_manager: The instance of the service to manage files via SSH.
            dry_run: If true, do not commit any changes.
        """
        self._db = database_client
        self._ssh = ssh_file_manager
        self._dry_run = dry_run

    async def get_event(self, event_url: str) -> Event:
        """Resolve an event URL to an Event instance.

        Args:
            event_url: The full URL of the event page.

        Returns:
            The Event fetched from the database.
        """
        LOGGER.info("Getting event from %s ...", event_url)

        url_without_query = event_url.split("?")[0]

        event_id_match = re.search(r"/(\d+)/?$", url_without_query)
        if event_id_match is None:
            raise GogolCLIException(f"Invalid event URL: {event_url}")
        event_id = event_id_match.group(1)

        event = await self._db.get_event_by_id(event_id)

        LOGGER.info("Finished getting event from %s", event_url)

        return event

    async def _copy_picture(self, session: AsyncSession, picture_id: int | None) -> int:
        """Fetch file metadata, copy the physical file via SSH, insert new DB record.

        Returns the new file ID.
        """
        if picture_id is None:
            raise ValueError("Cannot copy picture: picture_id is None")

        old_file = await self._db.get_file_by_id(session, picture_id)
        new_subdir = self._db.generate_new_subdir()

        if not self._dry_run:
            if self._ssh is None:
                raise SSHNotConfiguredError(
                    "An SSH file manager is required to copy pictures but was not provided."
                )
            await self._ssh.copy_file(old_file, new_subdir)

        return await self._db.insert_file_copy(session, picture_id, new_subdir)

    async def pin_event(self, event: Event) -> None:
        """Create a pin element for the given event, copying its preview picture.

        Args:
            event: The event to pin.
        """
        LOGGER.info("Pinning event %s ...", event.id)

        async with self._db.session() as session:
            preview_picture_id = await self._copy_picture(session, event.preview_picture)
            pin_id = await self._db.insert_pin(session, event, preview_picture_id)
            await self._db.set_pin_properties(session, event, pin_id)

            if not self._dry_run:
                await session.commit()

        LOGGER.info("Finished pinning event %s", event.id)

    async def copy_event(
        self,
        event: Event,
        new_event_date_str: str,
        new_event_time_str: str,
        new_price: str | None,
    ) -> None:
        """Copy an event to a new date, duplicating its pictures and properties.

        Args:
            event: The source event to copy.
            new_event_date_str: The date for the new event in YYYY-MM-DD format.
            new_event_time_str: The time for the new event in HH-MM format.
            new_price: The ticket price for the new event, or None to keep the original.
        """
        LOGGER.info("Copying event %s to %s ...", event.id, new_event_date_str)

        new_event_date = datetime.strptime(new_event_date_str, const.DATE_FORMAT)

        async with self._db.session() as session:
            preview_picture_id = await self._copy_picture(session, event.preview_picture)
            detail_picture_id = await self._copy_picture(session, event.detail_picture)

            new_event_id = await self._db.insert_event_copy(
                session,
                event,
                preview_picture_id,
                detail_picture_id,
                new_event_date,
                new_event_time_str,
            )
            await self._db.set_event_properties(
                session,
                event,
                new_event_id,
                new_event_date,
                new_event_time_str,
                new_price,
            )
            await self._db.add_element_to_section(
                session, new_event_id, const.EVENT_IBLOCK_SECTION_ID
            )

            if not self._dry_run:
                await session.commit()

        LOGGER.info("Finished copying event %s to %s", event.id, new_event_date_str)

    async def export(self, month_number: int, year_suffix: str) -> list[dict[str, int]]:
        """Collect activity statistics for the given month.

        Args:
            month_number: The calendar month number (1–12).
            year_suffix: The last two digits of the year (e.g. ``"25"``).

        Returns:
            A list of dicts with ``what`` and ``cnt`` keys.
        """
        LOGGER.info("Exporting monthly statistics for %s/%s ...", month_number, year_suffix)

        start_date, end_date = self._get_start_and_end_dates(month_number, year_suffix)
        statistics = await self._db.export_statistics(start_date, end_date)

        LOGGER.info("Finished exporting monthly statistics for %s/%s", month_number, year_suffix)

        return statistics

    @staticmethod
    def _get_start_and_end_dates(month_number: int, year_suffix: str) -> tuple[datetime, datetime]:
        """Get start and end dates for monthly statistics."""
        full_year = f"20{year_suffix}"

        start_date = datetime.strptime(f"{full_year}-{month_number}-01", const.DATE_FORMAT)

        if month_number == const.DECEMBER:
            next_month_start_date = datetime.strptime(
                f"{int(full_year) + 1}-01-01", const.DATE_FORMAT
            )
        else:
            next_month_start_date = datetime.strptime(
                f"{full_year}-{month_number + 1}-01", const.DATE_FORMAT
            )

        return start_date, next_month_start_date

    async def copy_chronograph(self, month_number: int, year_suffix: str) -> None:
        """Create a new chronograph section for the given month and copy entries from 5 years ago.

        Args:
            month_number: The calendar month number (1–12).
            year_suffix: The last two digits of the target year (e.g. ``"25"``).
        """
        LOGGER.info("Copying chronograph for %s/%s ...", month_number, year_suffix)

        old_full_year = f"20{int(year_suffix) - const.CHRONOGRAPH_YEAR_OFFSET}"
        new_full_year = f"20{year_suffix}"
        month_name = const.MONTH_NAMES[month_number]

        old_section_name = f"{month_name} {old_full_year}"
        new_section_name = f"{month_name} {new_full_year}"

        async with self._db.session() as session:
            await self._db.insert_chronograph_section(session, new_section_name)

            old_id = await self._db.get_chronograph_section_by_name(session, old_section_name)
            new_id = await self._db.get_chronograph_section_by_name(session, new_section_name)

            await self._db.copy_chronograph_section(session, old_id, new_id)

            if not self._dry_run:
                await session.commit()

        LOGGER.info("Finished copying chronograph for %s/%s", month_number, year_suffix)
