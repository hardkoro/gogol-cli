"""Clients."""

from itertools import count
from datetime import datetime, timedelta
import logging
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

from gogol_cli.schemas import Event, File
from gogol_cli.ssh_file_manager import SSHFileManager
from gogol_cli.exceptions import EventNotFoundError

DATE_FORMAT = "%d.%m.%Y"
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

LOGGER = logging.getLogger(__name__)


class DatabaseClient:
    """Database client."""

    PIN_IBLOCK_ID = 38
    EVENT_IBLOCK_ID = 6
    EVENT_IBLOCK_SECTION_ID = 7

    PIN_IBLOCK_SECTION_ID = None

    PIN_DEFAULT_SORT = 50
    EVENT_DEFAULT_SORT = 500

    DEFAULT_USER_ID = 1

    PIN_LINK_PROPERTY_ID = 150
    PIN_BUTTON_TEXT_PROPERTY_ID = 149
    PIN_NAME_PROPERTY_ID = 148

    EVENT_TIME_PROPERTY_ID = 14
    EVENT_DATE_PROPERTY_ID = 15
    EVENT_PRICE_PROPERTY_ID = 129

    CHRONOGRAPH_IBLOCK_ID = 8
    CHRONOGRAPH_YEAR_PROPERTY_ID = 23

    def __init__(
        self,
        ssh_file_manager: SSHFileManager,
        database_uri: str,
        dry_run: bool,
    ) -> None:
        """Initialize the client."""
        self._ssh_file_manager = ssh_file_manager

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
                b_iblock_element.preview_text,
                b_iblock_element.preview_text_type,
                b_iblock_element.detail_picture,
                b_iblock_element.detail_text,
                b_iblock_element.detail_text_type,
                b_iblock_element.tags
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
            preview_text=event["preview_text"],
            preview_text_type=event["preview_text_type"],
            detail_picture=event["detail_picture"],
            detail_text=event["detail_text"],
            detail_text_type=event["detail_text_type"],
            tags=event["tags"],
        )

    async def pin_event(self, event: Event) -> None:
        """Pin event."""
        LOGGER.info("Pinning event %s ...", event.id)

        async with self._session_maker() as session:
            preview_picture_id = await self._copy_preview_picture(session, event)
            pinned_event_id = await self._pin_event(session, event, preview_picture_id)
            await self._set_properties(session, event, pinned_event_id)

            if not self._dry_run:
                await session.commit()

        LOGGER.info("Finished pinning event %s", event.id)

    async def _copy_detail_picture(self, session: AsyncSession, event: Event) -> int:
        """Copy detail picture."""
        return await self._copy_picture(session, event.detail_picture)

    async def _copy_preview_picture(self, session: AsyncSession, event: Event) -> int:
        """Copy preview picture."""
        return await self._copy_picture(session, event.preview_picture)

    async def _copy_picture(self, session: AsyncSession, picture_id: int | None) -> int:
        """Copy picture."""
        if picture_id is None:
            raise ValueError("Cannot copy picture: picture_id is None")

        # A: Determine old file
        old_file = await self._get_file_by_id(session, picture_id)

        # B: Generate new unique subdir
        new_subdir = self._generate_new_subdir()

        # C: Copy physical file
        await self._ssh_file_manager.copy_file(old_file, new_subdir)

        # D. Insert new `b_file` record
        query = text(
            """
                 INSERT INTO b_file(timestamp_x, module_id, height, width, file_size, content_type, subdir,
                                    file_name, original_name, description, handler_id, external_id)
                 SELECT timestamp_x, module_id, height, width, file_size, content_type, :subdir,
                        file_name, original_name, description, handler_id, external_id
                 FROM b_file
                 WHERE id = :id
            """
        )
        await session.execute(query, {"subdir": new_subdir, "id": picture_id})

        return await self._get_last_insert_id(session)

    async def _pin_event(
        self, session: AsyncSession, event: Event, preview_picture_id: int
    ) -> int:
        """Pin event."""
        now = datetime.now(tz=None).strftime(DATETIME_FORMAT)
        user = self.DEFAULT_USER_ID
        active_to = event.active_to - timedelta(hours=1)
        query = f"""
            INSERT INTO b_iblock_element(timestamp_x, modified_by, date_create, created_by, iblock_id, active, active_from, active_to, sort, name, preview_picture, searchable_content, tmp_id)
            VALUES ('{now}', '{user}', '{now}', '{user}', '{self.PIN_IBLOCK_ID}', 'Y', '{event.active_from}', '{active_to}', '{self.PIN_DEFAULT_SORT}', '{event.name}', {preview_picture_id}, '{event.name.upper()}', 0);
        """
        await session.execute(text(query))

        return await self._get_last_insert_id(session)

    async def _copy_event(  # pylint: disable=too-many-locals
        self,
        session: AsyncSession,
        event: Event,
        preview_picture_id: int,
        detail_picture_id: int,
        new_event_date: datetime,
        new_event_time: str,
    ) -> int:
        """Copy event."""
        now = datetime.now(tz=None).strftime(DATETIME_FORMAT)
        user = self.DEFAULT_USER_ID
        hours, minutes = new_event_time.split("-")
        active_to_datetime = new_event_date + timedelta(hours=int(hours) + 1, minutes=int(minutes))
        active_to = active_to_datetime.strftime(DATETIME_FORMAT)

        query = f"""
            INSERT INTO b_iblock_element(timestamp_x, modified_by, date_create, created_by, iblock_id, iblock_section_id, active, active_from, active_to, sort, name, preview_picture, preview_text, preview_text_type, detail_picture, detail_text, detail_text_type, searchable_content, tags, tmp_id)
            VALUES ('{now}', '{user}', '{now}', '{user}', '{self.EVENT_IBLOCK_ID}', '{self.EVENT_IBLOCK_SECTION_ID}', 'Y', '{now}', '{active_to}', '{self.EVENT_DEFAULT_SORT}', '{event.name}', {preview_picture_id}, '{event.preview_text}', '{event.preview_text_type}', {detail_picture_id}, '{event.detail_text}', '{event.detail_text_type}', '{event.name.upper()}', '{event.tags}', 0);
        """
        await session.execute(text(query))

        return await self._get_last_insert_id(session)

    async def _set_properties(
        self,
        session: AsyncSession,
        event: Event,
        pin_id: int,
    ) -> None:
        """Copy properties from event to pin."""
        query = f"""
            UPDATE b_iblock_element
            SET xml_id = '{pin_id}'
            WHERE id = '{pin_id}';
            INSERT INTO b_iblock_element_property (iblock_property_id, iblock_element_id, value, value_type, value_num)
            VALUES ('{self.PIN_LINK_PROPERTY_ID}', '{pin_id}', '{event.url}', 'text', 0.0000)
            ,('{self.PIN_BUTTON_TEXT_PROPERTY_ID}', '{pin_id}', 'Подробнее', 'text', 0.0000)
            ,('{self.PIN_NAME_PROPERTY_ID}', '{pin_id}', '{event.name}', 'text', 0.0000);
        """

        await session.execute(text(query))

    async def _copy_properties(
        self,
        session: AsyncSession,
        old_event: Event,
        new_event_id: int,
        new_event_date: datetime,
        new_event_time: str,
        new_event_price: str | None,
    ) -> None:
        """Copy properties from event to event."""
        query = f"""
            UPDATE b_iblock_element
            SET xml_id = '{new_event_id}'
            WHERE id = '{new_event_id}';
            INSERT INTO b_iblock_element_property (iblock_property_id, iblock_element_id, value, value_type, value_num)
            SELECT iblock_property_id, '{new_event_id}', value, value_type, value_num
            FROM b_iblock_element_property
            WHERE iblock_element_id = '{old_event.id}';
        """

        await session.execute(text(query))

        query = f"""
            UPDATE b_iblock_element_property
            SET value = '{new_event_time.replace("-", ":")}'
            WHERE iblock_element_id = '{new_event_id}'
            AND iblock_property_id = '{self.EVENT_TIME_PROPERTY_ID}';
        """

        await session.execute(text(query))

        query = f"""
            UPDATE b_iblock_element_property
            SET value = '{new_event_date.strftime(DATE_FORMAT)}'
            WHERE iblock_element_id = '{new_event_id}'
            AND iblock_property_id = '{self.EVENT_DATE_PROPERTY_ID}';
        """

        await session.execute(text(query))

        if new_event_price is not None:
            query = f"""
                UPDATE b_iblock_element_property
                SET value = '{new_event_price}'
                WHERE iblock_element_id = '{new_event_id}'
                AND iblock_property_id = '{self.EVENT_PRICE_PROPERTY_ID}';
            """

            await session.execute(text(query))

    async def copy_event(
        self,
        old_event: Event,
        new_event_date: datetime,
        new_event_time: str,
        new_event_price: str | None,
    ) -> None:
        """Copy event."""
        LOGGER.info("Copying event %s to %s ...", old_event.id, new_event_date)

        async with self._session_maker() as session:
            preview_picture_id = await self._copy_preview_picture(session, old_event)
            detail_picture_id = await self._copy_detail_picture(session, old_event)

            new_event_id = await self._copy_event(
                session,
                old_event,
                preview_picture_id,
                detail_picture_id,
                new_event_date,
                new_event_time,
            )
            await self._copy_properties(
                session,
                old_event,
                new_event_id,
                new_event_date,
                new_event_time,
                new_event_price,
            )
            await self._add_element_to_section(session, new_event_id, self.EVENT_IBLOCK_SECTION_ID)

            if not self._dry_run:
                await session.commit()

        LOGGER.info("Finished copying event %s to %s", old_event.id, new_event_date)

    @staticmethod
    async def _add_element_to_section(
        session: AsyncSession,
        element_id: int,
        section_id: int,
    ) -> None:
        """Add element to section."""
        query = f"""
            INSERT INTO b_iblock_section_element (iblock_section_id, iblock_element_id, additional_property_id)
            VALUES ('{section_id}', '{element_id}', NULL);
        """

        await session.execute(text(query))

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
                VALUES (NOW(), 1, NOW(), 1, {self.CHRONOGRAPH_IBLOCK_ID}, NULL, 'Y', 'Y', 500, '{section_name}', NULL, 1, '{section_name.upper()}', 0, NULL, NULL);
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
        self,
        new_chronograph_section_id: int,
        previous_chronograph_section_id: int,
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
            query = f"""
                INSERT INTO b_iblock_section_element (iblock_section_id, iblock_element_id, additional_property_id)
                SELECT {new_chronograph_section_id}, id, NULL
                FROM b_iblock_element
                WHERE iblock_section_id = {new_chronograph_section_id};
            """
            await session.execute(text(query))

            affected_elements = await self._get_affected_elements(new_chronograph_section_id)

            for element_id in affected_elements:
                query = f"""
                    UPDATE b_iblock_element_property
                    SET value = value + 5
                    WHERE iblock_element_id = {element_id}
                      AND iblock_property_id = {self.CHRONOGRAPH_YEAR_PROPERTY_ID};
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

    @staticmethod
    async def _get_last_insert_id(session: AsyncSession) -> int:
        """Get the last inserted row ID."""
        result = await session.execute(text("SELECT LAST_INSERT_ID();"))
        return int(result.scalar_one())

    @staticmethod
    async def _get_file_by_id(session: AsyncSession, file_id: int) -> File:
        """Get file by id."""
        query = text("SELECT * FROM b_file WHERE ID = :file_id")
        result = await session.execute(query, {"file_id": file_id})
        row = result.mappings().fetchone()

        if not row:
            LOGGER.info("File %s not found", file_id)
            raise FileNotFoundError(f"File ID {file_id} not found")

        return File(
            id=row["ID"],
            timestamp=row["TIMESTAMP_X"],
            module_id=row["MODULE_ID"],
            height=row["HEIGHT"],
            width=row["WIDTH"],
            file_size=row["FILE_SIZE"],
            content_type=row["CONTENT_TYPE"],
            subdir=row["SUBDIR"],
            file_name=row["FILE_NAME"],
            original_name=row["ORIGINAL_NAME"],
            external_id=row["EXTERNAL_ID"],
        )

    @staticmethod
    def _generate_new_subdir() -> str:
        """Generate new subdir."""
        file_hash = uuid4().hex
        subdir = f"iblock/{file_hash[:3]}/{file_hash[3:6]}/{file_hash}"
        return subdir
