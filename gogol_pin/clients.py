"""Clients."""

from itertools import count
from datetime import datetime
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

from gogol_pin.schemas import Event
from gogol_pin.exceptions import EventNotFoundError


LOGGER = logging.getLogger(__name__)


class DatabaseClient:
    """Database client."""

    PIN_IBLOCK_ID = 38
    PIN_IBLOCK_SECTION_ID = None
    PIN_DEFAULT_SORT = 50
    DEFAULT_USER_ID = 1

    PIN_LINK_PROPERTY_ID = 150
    PIN_BUTTON_TEXT_PROPERTY_ID = 149
    PIN_NAME = 148

    def __init__(self, database_uri: str, dry_run: bool) -> None:
        """Initialize the client."""
        self._engine = create_async_engine(database_uri, echo=True)
        self._session_maker = async_sessionmaker(self._engine)
        self._dry_run = dry_run

    async def _query(self, query: str) -> list[dict]:
        """Query the database."""
        async with self._session_maker() as session:
            rows = (await session.execute(text(query))).all()

        results: list[dict] = []
        for row in rows:
            result: dict[str, object] = {}
            for column, value in zip(row._fields, row):
                if column not in result:
                    result[column] = value
                else:
                    # When a query has joins and the two tables have columns
                    # with similar names, SQLAlchemy doesn't add prefix / suffix
                    # to them and returns the name of the column twice. To make sure
                    # the data is not missed, a numeric postfix is added to the column
                    # name here.
                    for i in count(2):
                        col = f"{column}{i}"
                        if col not in result:
                            result[col] = value
                            break

            results.append(result)

        return results

    async def get_event_by_id(self, event_id: str) -> Event:
        """Get event by ID."""
        LOGGER.info("Getting event ID %s from the database ...", event_id)

        query = f"""
            SELECT
                b_iblock_element.id,
                b_iblock_element.name,
                b_iblock_element.active_from,
                b_iblock_element.active_to,
                b_iblock_element.preview_picture,
                b_iblock_element.detail_picture,
                b_iblock_element.detail_text,
                b_iblock_element.detail_text_type
            FROM
                b_iblock_element
            WHERE
                b_iblock_element.id = '{event_id}'
            LIMIT 1;
        """

        events = await self._query(query)

        if len(events) == 0:
            raise EventNotFoundError(f"Event ID {event_id} not found")

        event = events[0]

        LOGGER.info("Finished getting event ID %s from the database", event_id)

        return Event(
            id=event["id"],
            name=event["name"],
            active_from=event["active_from"],
            active_to=event["active_to"],
            preview_picture=event["preview_picture"],
            detail_picture=event["detail_picture"],
            detail_text=event["detail_text"],
            detail_text_type=event["detail_text_type"],
        )

    async def pin_event(self, event: Event) -> None:
        """Pin event."""
        LOGGER.info("Pinning event %s ...", event.id)

        async with self._session_maker() as session:
            preview_picture_id = await self._copy_preview_picture(session, event)
            pinned_event_id = await self._pin_event(session, event, preview_picture_id)
            await self._copy_properties_from_event_to_pin(session, event, pinned_event_id)

            if not self._dry_run:
                await session.commit()

        LOGGER.info("Finished pinning event %s", event.id)

    async def _copy_preview_picture(self, session: AsyncSession, event: Event) -> int:
        """Copy preview picture."""
        query = f"""
            INSERT INTO b_file(timestamp_x, module_id, height, width, file_size, content_type, subdir, file_name, original_name, description, handler_id, external_id)
            SELECT timestamp_x, module_id, height, width, file_size, content_type, subdir, file_name, original_name, description, handler_id, external_id
            FROM b_file
            WHERE id = '{event.preview_picture}';
        """
        await session.execute(text(query))

        query = """
            SELECT MAX(id) AS `id`
            FROM b_file;
        """

        preview_picture_query = await self._query(query)
        preview_picture_id = preview_picture_query[0]["id"]

        return int(preview_picture_id)

    async def _pin_event(
        self, session: AsyncSession, event: Event, preview_picture_id: int
    ) -> int:
        """Pin event."""
        query = f"""
            INSERT INTO b_iblock_element(timestamp_x, modified_by, date_create, created_by, iblock_id, active, active_from, active_to, sort, name, preview_picture, searchable_content, tmp_id)
            VALUES ('{datetime.now(tz=None).strftime('%Y-%m-%d %H:%M:%S')}', '{self.DEFAULT_USER_ID}', '{datetime.now(tz=None).strftime('%Y-%m-%d %H:%M:%S')}', '{self.DEFAULT_USER_ID}', '{self.PIN_IBLOCK_ID}', 'Y', '{event.active_from}', '{event.active_to}', '{self.PIN_DEFAULT_SORT}', '{event.name}', {preview_picture_id}, '{event.name.upper()}', 0);
        """
        await session.execute(text(query))

        query = """
            SELECT MAX(id) AS `id`
            FROM b_iblock_element;
        """
        events = await self._query(query)
        event_id = events[0]["id"]

        return int(event_id)

    async def _copy_properties_from_event_to_pin(
        self, session: AsyncSession, event: Event, pinned_event_id: int
    ) -> None:
        """Copy properties from event to pin."""
        query = f"""
            UPDATE b_iblock_element
            SET xml_id = '{pinned_event_id}'
            WHERE id = '{pinned_event_id}';
            INSERT INTO b_iblock_element_property (iblock_property_id, iblock_element_id, value, value_type, value_num)
            VALUES ('{self.PIN_LINK_PROPERTY_ID}', '{pinned_event_id}', '{event.url}', 'text', 0.0000),
            ('{self.PIN_BUTTON_TEXT_PROPERTY_ID}', '{pinned_event_id}', 'Подробнее', 'text', 0.0000),
            ('{self.PIN_NAME}', '{pinned_event_id}', '{event.name}', 'text', 0.0000);
        """
        await session.execute(text(query))

    async def copy_event(self, event: Event, new_event_datetime: datetime) -> None:
        """Copy event."""
        LOGGER.info("Copying event %s to %s ...", event.id, new_event_datetime)

        async with self._session_maker() as session:
            if not self._dry_run:
                await session.commit()

        LOGGER.info("Finished copying event %s to %s", event.id, new_event_datetime)

    async def export_statistics(
        self, start_date: datetime, end_date: datetime
    ) -> list[dict[str, int]]:
        """Export monthly statistics."""
        LOGGER.info("Exporting monthly statistics from %s to %s ...", start_date, end_date)

        # Format the dates as strings in 'YYYY-MM-DD' format for the SQL query
        start_date_str = start_date.strftime("%Y-%m-%d")
        end_date_str = end_date.strftime("%Y-%m-%d")

        query = f"""
            SELECT whats.what
              , COUNT(*) AS cnt
            FROM (
                SELECT '02 files' AS what
                  , b_file.TIMESTAMP_X AS timestamp
                FROM b_file
                UNION ALL
                SELECT '04 search changes' AS what
                  , b_search_content.DATE_CHANGE AS timestamp
                FROM b_search_content
                UNION ALL
                SELECT '01 added' AS what
                  , b_iblock_element.DATE_CREATE AS timestamp
                FROM b_iblock_element
                UNION ALL
                SELECT '03 updated' AS what
                  , b_iblock_element.TIMESTAMP_X AS timestamp
                FROM b_iblock_element) AS whats
            WHERE whats.timestamp BETWEEN STR_TO_DATE('{start_date_str}','%Y-%m-%d') AND STR_TO_DATE('{end_date_str}','%Y-%m-%d')
            GROUP BY whats.what
            ORDER BY whats.what;
        """

        statistics = await self._query(query)

        LOGGER.info("Finished exporting monthly statistics from %s to %s", start_date, end_date)
        LOGGER.info(statistics)

        return statistics

    async def add_chronograph_section(self, section_name: str) -> None:
        """Add chronograph section."""
        LOGGER.info("Adding chronograph section %s ...", section_name)

        async with self._session_maker() as session:
            query = f"""
                INSERT INTO b_iblock_section(timestamp_x, modified_by, date_create, created_by, iblock_id, iblock_section_id, active, global_active, sort, name, picture, depth_level, searchable_content, tmp_id, detail_picture, socnet_group_id)
                VALUES (NOW(), 1, NOW(), 1, 8, NULL, 'Y', 'Y', 500, '{section_name}', NULL, 1, '{section_name.upper()}', 0, NULL, NULL);
            """
            await session.execute(text(query))

            if not self._dry_run:
                await session.commit()

        LOGGER.info("Finished adding chronograph section %s", section_name)

    async def get_chronograph_section_by_name(self, section_name: str) -> int:
        """Get chronograph section by name."""
        query = f"""
            SELECT id
            FROM b_iblock_section
            WHERE name = '{section_name}';
        """
        chronograph_section = await self._query(query)

        return chronograph_section[0]["id"]

    async def copy_chronograph_section(
        self, new_chronograph_section_id: int, previous_chronograph_section_id: int
    ) -> None:
        """Copy chronograph section."""
        LOGGER.info(
            "Copying chronograph section %s to %s ...",
            previous_chronograph_section_id,
            new_chronograph_section_id,
        )

        async with self._session_maker() as session:
            query = f"""
                UPDATE b_iblock_element
                SET iblock_section_id = {new_chronograph_section_id}
                  , modified_by = 1
                  , date_create = NOW()
                  , created_by = 1
                  , active = 'Y'
                  , active_from = active_from + INTERVAL 5 YEAR
                  , active_to = active_to + INTERVAL 5 YEAR
                WHERE iblock_section_id = {previous_chronograph_section_id};
            """
            await session.execute(text(query))

            affected_elements = await self._get_affected_elements(new_chronograph_section_id)

            for element_id in affected_elements:
                query = f"""
                    UPDATE b_iblock_element_property
                    SET value = value + 5
                    WHERE iblock_element_id = {element_id}
                      AND iblock_property_id = 23;
                """
                await session.execute(text(query))

            query = f"""
                UPDATE b_iblock_section
                SET active = 'N'
                WHERE id = {previous_chronograph_section_id};
            """
            await session.execute(text(query))

            if not self._dry_run:
                await session.commit()

        LOGGER.info(
            "Finished copying chronograph section %s to %s",
            previous_chronograph_section_id,
            new_chronograph_section_id,
        )

    async def _get_affected_elements(self, new_chronograph_section_id: int) -> list[int]:
        """Get affected elements."""
        query = f"""
            SELECT id
            FROM b_iblock_element
            WHERE iblock_section_id = {new_chronograph_section_id};
        """
        affected_elements = await self._query(query)

        return [element["id"] for element in affected_elements]
