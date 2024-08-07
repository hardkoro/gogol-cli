"""Clients."""

from itertools import count
from datetime import datetime
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
        LOGGER.info(f"Getting event ID {event_id} from the database ...")

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

        LOGGER.info(f"Finished getting event ID {event_id} from the database")

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
        LOGGER.info(f"Pinning event {event.id} ...")

        query = f"""
            INSERT INTO b_file(timestamp_x, module_id, height, width, file_size, content_type, subdir, file_name, original_name, description, handler_id, external_id)
            SELECT timestamp_x, module_id, height, width, file_size, content_type, subdir, file_name, original_name, description, handler_id, external_id
            FROM b_file
            WHERE id = '{event.preview_picture}';
        """

        async with self._session_maker() as session:
            await session.execute(text(query))
            if not self._dry_run:
                await session.commit()

        query = f"""
            SELECT MAX(id) AS `id`
            FROM b_file;
        """

        preview_picture_query = await self._query(query)

        preview_picture = preview_picture_query[0]["id"]

        query = f"""
            INSERT INTO b_iblock_element(timestamp_x, modified_by, date_create, created_by, iblock_id, active, active_from, active_to, sort, name, preview_picture, searchable_content, tmp_id)
            VALUES ('{datetime.now(tz=None).strftime('%Y-%m-%d %H:%M:%S')}', '{self.DEFAULT_USER_ID}', '{datetime.now(tz=None).strftime('%Y-%m-%d %H:%M:%S')}', '{self.DEFAULT_USER_ID}', '{self.PIN_IBLOCK_ID}', 'Y', '{event.active_from}', '{event.active_to}', '{self.PIN_DEFAULT_SORT}', '{event.name}', {preview_picture}, '{event.name.upper()}', 0);
        """

        async with self._session_maker() as session:
            await session.execute(text(query))
            if not self._dry_run:
                await session.commit()

        query = f"""
            SELECT MAX(id) AS `id`
            FROM b_iblock_element;
        """

        events = await self._query(query)

        event_id = events[0]["id"]

        query = f"""
            UPDATE b_iblock_element
            SET xml_id = '{event_id}'
            WHERE id = '{event_id}';
            INSERT INTO b_iblock_element_property (iblock_property_id, iblock_element_id, value, value_type, value_num)
            VALUES ('{self.PIN_LINK_PROPERTY_ID}', '{event_id}', '{event.url}', 'text', 0.0000),
            ('{self.PIN_BUTTON_TEXT_PROPERTY_ID}', '{event_id}', 'Подробнее', 'text', 0.0000),
            ('{self.PIN_NAME}', '{event_id}', '{event.name}', 'text', 0.0000);
        """

        async with self._session_maker() as session:
            await session.execute(text(query))
            if not self._dry_run:
                await session.commit()

        LOGGER.info(f"Finished pinning event {event.id}")
