"""Clients."""

import logging
import re
from datetime import datetime, timedelta
from itertools import count
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gogol_cli import constants as const
from gogol_cli.exceptions import DBEventNotFoundError
from gogol_cli.schemas import Event, File

LOGGER = logging.getLogger(__name__)


class DatabaseClient:
    """Database client."""

    def __init__(self, database_uri: str) -> None:
        """Initialize the client.

        Args:
            database_uri: Database URI.
        """
        self._engine = create_async_engine(database_uri, echo=False)
        self._session_maker = async_sessionmaker(self._engine)

    def session(self) -> AsyncSession:
        """Return an async session context manager.

        Returns:
            An async session that can be used as an async context manager.
        """
        return self._session_maker()

    async def _query(self, query: str, params: dict | None = None) -> list[dict]:
        """Execute a read-only parameterised query and return rows as dicts."""
        async with self._session_maker() as session:
            rows = (await session.execute(text(query), params or {})).all()

        results: list[dict] = []
        for row in rows:
            result: dict[str, object] = {}
            for column, value in zip(row._fields, row):
                if column not in result:
                    result[column] = value
                else:
                    for i in count(2):
                        col = f"{column}{i}"
                        if col not in result:
                            result[col] = value
                            break
            results.append(result)

        return results

    async def get_event_by_id(self, event_id: str) -> Event:
        """Fetch a single event record from the database by its numeric ID.

        Args:
            event_id: The numeric event ID as a string.

        Returns:
            The matching Event instance.
        """
        LOGGER.info("Getting event ID %s from the database ...", event_id)

        async with self._session_maker() as session:
            result = await session.execute(
                text(
                    """
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
                    FROM b_iblock_element
                    WHERE b_iblock_element.id = :event_id
                """
                ),
                {"event_id": event_id},
            )
            row = result.mappings().fetchone()

        if row is None:
            raise DBEventNotFoundError(f"Event ID {event_id} not found")

        LOGGER.info("Finished getting event ID %s from the database", event_id)

        return Event.model_validate(row)

    @staticmethod
    async def get_file_by_id(session: AsyncSession, file_id: int) -> File:
        """Fetch a single file record from the database by its ID.

        Args:
            session: The active database session.
            file_id: The numeric file ID.

        Returns:
            The matching File instance.
        """
        result = await session.execute(
            text("SELECT * FROM b_file WHERE ID = :file_id"),
            {"file_id": file_id},
        )
        row = result.mappings().fetchone()

        if not row:
            raise FileNotFoundError(f"File ID {file_id} not found")

        return File.model_validate(row)

    @staticmethod
    async def insert_file_copy(session: AsyncSession, original_id: int, new_subdir: str) -> int:
        """Insert a copy of a b_file record pointing to a new subdirectory.

        Args:
            session: The active database session.
            original_id: The ID of the source file record to copy.
            new_subdir: The subdirectory path for the new file record.

        Returns:
            The ID of the newly inserted file record.
        """
        await session.execute(
            text(
                """
                INSERT INTO b_file (
                    timestamp_x, module_id, height, width, file_size, content_type,
                    subdir, file_name, original_name, description, handler_id, external_id
                )
                SELECT
                    timestamp_x, module_id, height, width, file_size, content_type,
                    :subdir, file_name, original_name, description, handler_id, external_id
                FROM b_file
                WHERE id = :original_id
            """
            ),
            {"subdir": new_subdir, "original_id": original_id},
        )
        return await DatabaseClient._get_last_insert_id(session)

    @staticmethod
    async def insert_pin(session: AsyncSession, event: Event, preview_picture_id: int) -> int:
        """Insert a new pin element linked to an event.

        Args:
            session: The active database session.
            event: The source event to create a pin for.
            preview_picture_id: The file ID to use as the pin's preview picture.

        Returns:
            The ID of the newly inserted pin element.
        """
        now = datetime.now(tz=None).strftime(const.DATETIME_FORMAT)
        active_to = (event.active_to - timedelta(hours=1)).strftime(const.DATETIME_FORMAT)

        await session.execute(
            text(
                """
                INSERT INTO b_iblock_element (
                    timestamp_x, modified_by, date_create, created_by,
                    iblock_id, active, active_from, active_to,
                    sort, name, preview_picture, searchable_content, tmp_id
                )
                VALUES (
                    :now, :user, :now, :user,
                    :iblock_id, 'Y', :active_from, :active_to,
                    :sort, :name, :preview_picture, :searchable_content, 0
                )
            """
            ),
            {
                "now": now,
                "user": const.DEFAULT_USER_ID,
                "iblock_id": const.PIN_IBLOCK_ID,
                "active_from": str(event.active_from),
                "active_to": active_to,
                "sort": const.PIN_DEFAULT_SORT,
                "name": event.name,
                "preview_picture": preview_picture_id,
                "searchable_content": event.name.upper(),
            },
        )
        return await DatabaseClient._get_last_insert_id(session)

    @staticmethod
    async def set_pin_properties(session: AsyncSession, event: Event, pin_id: int) -> None:
        """Set the link, button text, and name properties on a pin element.

        Args:
            session: The active database session.
            event: The source event whose data is used to populate the properties.
            pin_id: The ID of the pin element to update.
        """
        await session.execute(
            text("UPDATE b_iblock_element SET xml_id = :pin_id WHERE id = :pin_id"),
            {"pin_id": pin_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO b_iblock_element_property
                    (iblock_property_id, iblock_element_id, value, value_type, value_num)
                VALUES
                    (:link_prop_id,  :pin_id, :url,        'text', 0.0000),
                    (:btn_prop_id,   :pin_id, 'Подробнее', 'text', 0.0000),
                    (:name_prop_id,  :pin_id, :name,       'text', 0.0000)
            """
            ),
            {
                "link_prop_id": const.PIN_LINK_PROPERTY_ID,
                "btn_prop_id": const.PIN_BUTTON_TEXT_PROPERTY_ID,
                "name_prop_id": const.PIN_NAME_PROPERTY_ID,
                "pin_id": pin_id,
                "url": event.url,
                "name": event.name,
            },
        )

    @staticmethod
    async def insert_event_copy(
        session: AsyncSession,
        event: Event,
        preview_picture_id: int,
        detail_picture_id: int,
        new_event_date: datetime,
        new_event_time: str,
    ) -> int:
        """Insert a copy of an event element with a new date and time.

        Args:
            session: The active database session.
            event: The source event to copy.
            preview_picture_id: The file ID to use as the new event's preview picture.
            detail_picture_id: The file ID to use as the new event's detail picture.
            new_event_date: The date of the new event.
            new_event_time: The time of the new event in HH-MM format.

        Returns:
            The ID of the newly inserted event element.
        """
        now = datetime.now(tz=None).strftime(const.DATETIME_FORMAT)
        hours, minutes = new_event_time.split("-")
        active_from = now
        active_to = (
            new_event_date + timedelta(hours=int(hours) + 1, minutes=int(minutes))
        ).strftime(const.DATETIME_FORMAT)

        def _strip_html(s: str | None) -> str:
            return re.sub(r"<[^>]+>", " ", s or "")

        searchable_content = " ".join(
            filter(
                None,
                [
                    event.name,
                    _strip_html(event.preview_text),
                    _strip_html(event.detail_text),
                ],
            )
        ).upper()

        await session.execute(
            text(
                """
                INSERT INTO b_iblock_element (
                    timestamp_x, modified_by, date_create, created_by,
                    iblock_id, iblock_section_id, active, active_from, active_to,
                    sort, name, preview_picture, preview_text, preview_text_type,
                    detail_picture, detail_text, detail_text_type,
                    searchable_content, tags, tmp_id, code
                )
                VALUES (
                    :now, :user, :now, :user,
                    :iblock_id, NULL, 'Y', :active_from, :active_to,
                    :sort, :name, :preview_picture, :preview_text, :preview_text_type,
                    :detail_picture, :detail_text, :detail_text_type,
                    :searchable_content, :tags, 0, ''
                )
            """
            ),
            {
                "now": now,
                "user": const.DEFAULT_USER_ID,
                "iblock_id": const.EVENT_IBLOCK_ID,
                "active_from": active_from,
                "active_to": active_to,
                "sort": const.EVENT_DEFAULT_SORT,
                "name": event.name,
                "preview_picture": preview_picture_id,
                "preview_text": event.preview_text,
                "preview_text_type": event.preview_text_type,
                "detail_picture": detail_picture_id,
                "detail_text": event.detail_text,
                "detail_text_type": event.detail_text_type,
                "searchable_content": searchable_content,
                "tags": event.tags,
            },
        )
        new_event_id = await DatabaseClient._get_last_insert_id(session)

        # Insert b_search_content so the calendar filter picks up the new event
        url = (
            f"=ID={new_event_id}&EXTERNAL_ID={new_event_id}"
            f"&IBLOCK_SECTION_ID={const.EVENT_IBLOCK_SECTION_ID}"
            f"&IBLOCK_TYPE_ID={const.EVENT_IBLOCK_TYPE_ID}"
            f"&IBLOCK_ID={const.EVENT_IBLOCK_ID}"
            f"&IBLOCK_CODE={const.EVENT_IBLOCK_CODE}"
            f"&IBLOCK_EXTERNAL_ID={const.EVENT_IBLOCK_EXTERNAL_ID}"
            f"&CODE="
        )
        body = " ".join(
            filter(
                None,
                [
                    _strip_html(event.preview_text),
                    _strip_html(event.detail_text),
                ],
            )
        )
        await session.execute(
            text(
                """
                INSERT INTO b_search_content (
                    date_change, module_id, item_id, custom_rank,
                    url, title, body, tags, param1, param2,
                    date_from, date_to
                )
                VALUES (
                    :now, 'iblock', :item_id, 0,
                    :url, :title, :body, :tags, :param1, :param2,
                    :date_from, :date_to
                )
            """
            ),
            {
                "now": now,
                "item_id": str(new_event_id),
                "url": url,
                "title": event.name,
                "body": body,
                "tags": event.tags,
                "param1": const.EVENT_IBLOCK_TYPE_ID,
                "param2": str(const.EVENT_IBLOCK_ID),
                "date_from": active_from,
                "date_to": active_to,
            },
        )

        return new_event_id

    @staticmethod
    async def set_event_properties(
        session: AsyncSession,
        old_event: Event,
        new_event_id: int,
        new_event_date: datetime,
        new_event_time: str,
        new_event_price: str | None,
    ) -> None:
        """Copy properties from a source event to a new event and update date, time, and price.

        Args:
            session: The active database session.
            old_event: The source event whose properties are copied.
            new_event_id: The ID of the newly created event to write properties to.
            new_event_date: The date to set on the new event's date property.
            new_event_time: The time in HH-MM format to set on the new event's time property.
            new_event_price: The price to set, or None to leave the copied value unchanged.
        """
        await session.execute(
            text("UPDATE b_iblock_element SET xml_id = :new_id WHERE id = :new_id"),
            {"new_id": new_event_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO b_iblock_element_property
                    (iblock_property_id, iblock_element_id, value, value_type, value_enum, value_num, description)
                SELECT iblock_property_id, :new_id, value, value_type, value_enum, value_num, description
                FROM b_iblock_element_property
                WHERE iblock_element_id = :old_id
            """
            ),
            {"new_id": new_event_id, "old_id": old_event.id},
        )
        await session.execute(
            text(
                """
                UPDATE b_iblock_element_property
                SET value = :time_value
                WHERE iblock_element_id = :event_id
                  AND iblock_property_id = :prop_id
            """
            ),
            {
                "time_value": new_event_time.replace("-", ":"),
                "event_id": new_event_id,
                "prop_id": const.EVENT_TIME_PROPERTY_ID,
            },
        )
        await session.execute(
            text(
                """
                UPDATE b_iblock_element_property
                SET value = :date_value
                WHERE iblock_element_id = :event_id
                  AND iblock_property_id = :prop_id
            """
            ),
            {
                "date_value": new_event_date.strftime(const.DATETIME_FORMAT),
                "event_id": new_event_id,
                "prop_id": const.EVENT_DATE_PROPERTY_ID,
            },
        )
        if new_event_price is not None:
            await session.execute(
                text(
                    """
                    UPDATE b_iblock_element_property
                    SET value = :price
                    WHERE iblock_element_id = :event_id
                      AND iblock_property_id = :prop_id
                """
                ),
                {
                    "price": new_event_price,
                    "event_id": new_event_id,
                    "prop_id": const.EVENT_PRICE_PROPERTY_ID,
                },
            )

    @staticmethod
    async def add_element_to_section(
        session: AsyncSession, element_id: int, section_id: int
    ) -> None:
        """Add an iblock element to a section.

        Args:
            session: The active database session.
            element_id: The ID of the element to add.
            section_id: The ID of the section to add the element to.
        """
        await session.execute(
            text(
                """
                INSERT INTO b_iblock_section_element
                    (iblock_section_id, iblock_element_id, additional_property_id)
                VALUES (:section_id, :element_id, NULL)
            """
            ),
            {"section_id": section_id, "element_id": element_id},
        )

    async def export_statistics(
        self, start_date: datetime, end_date: datetime
    ) -> list[dict[str, int]]:
        """Query activity statistics for a given date range.

        Args:
            start_date: The start of the reporting period (inclusive).
            end_date: The end of the reporting period (exclusive).

        Returns:
            A list of dicts, each with a ``what`` label and a ``cnt`` count.
        """
        LOGGER.info("Exporting monthly statistics from %s to %s ...", start_date, end_date)

        query = """
            SELECT whats.what, COUNT(*) AS cnt
            FROM (
                SELECT '02 files'          AS what, b_file.TIMESTAMP_X           AS timestamp FROM b_file
                UNION ALL
                SELECT '04 search changes' AS what, b_search_content.DATE_CHANGE  AS timestamp FROM b_search_content
                UNION ALL
                SELECT '01 added'          AS what, b_iblock_element.DATE_CREATE  AS timestamp FROM b_iblock_element
                UNION ALL
                SELECT '03 updated'        AS what, b_iblock_element.TIMESTAMP_X  AS timestamp FROM b_iblock_element
            ) AS whats
            WHERE whats.timestamp BETWEEN :start_date AND :end_date
            GROUP BY whats.what
            ORDER BY whats.what
        """

        statistics = await self._query(
            query,
            {
                "start_date": start_date.strftime("%Y-%m-%d"),
                "end_date": end_date.strftime("%Y-%m-%d"),
            },
        )

        LOGGER.info("Finished exporting monthly statistics from %s to %s", start_date, end_date)
        LOGGER.info(statistics)

        return statistics

    @staticmethod
    async def insert_chronograph_section(session: AsyncSession, section_name: str) -> None:
        """Insert a new chronograph section with the given name.

        Args:
            session: The active database session.
            section_name: The human-readable name for the new section.
        """
        LOGGER.info("Adding chronograph section %s ...", section_name)

        await session.execute(
            text(
                """
                INSERT INTO b_iblock_section (
                    timestamp_x, modified_by, date_create, created_by,
                    iblock_id, iblock_section_id, active, global_active,
                    sort, name, picture, depth_level, searchable_content,
                    tmp_id, detail_picture, socnet_group_id
                )
                VALUES (
                    NOW(), :user, NOW(), :user,
                    :iblock_id, NULL, 'Y', 'Y',
                    :sort, :name, NULL, 1, :searchable_content,
                    0, NULL, NULL
                )
            """
            ),
            {
                "user": const.DEFAULT_USER_ID,
                "iblock_id": const.CHRONOGRAPH_IBLOCK_ID,
                "sort": const.CHRONOGRAPH_DEFAULT_SORT,
                "name": section_name,
                "searchable_content": section_name.upper(),
            },
        )

        LOGGER.info("Finished adding chronograph section %s", section_name)

    @staticmethod
    async def get_chronograph_section_by_name(session: AsyncSession, section_name: str) -> int:
        """Look up the ID of a chronograph section by its name.

        Args:
            session: The active database session.
            section_name: The exact name of the section to look up.

        Returns:
            The ID of the matching chronograph section.
        """
        result = await session.execute(
            text("SELECT id FROM b_iblock_section WHERE name = :name"),
            {"name": section_name},
        )
        row = result.fetchone()
        if row is None:
            raise ValueError(f"Chronograph section '{section_name}' not found")
        return row[0]

    @staticmethod
    async def copy_chronograph_section(
        session: AsyncSession,
        source_section_id: int,
        destination_section_id: int,
    ) -> None:
        """Copy all elements from a source chronograph section into a destination section.

        Args:
            session: The active database session.
            source_section_id: The ID of the section to copy elements from.
            destination_section_id: The ID of the section to copy elements into.
        """
        LOGGER.info(
            "Copying chronograph section %s into %s ...",
            source_section_id,
            destination_section_id,
        )

        await session.execute(
            text(
                """
                UPDATE b_iblock_element
                SET iblock_section_id = :dest_id,
                    modified_by       = :user,
                    date_create       = NOW(),
                    created_by        = :user,
                    active            = 'Y',
                    active_from       = active_from + INTERVAL 5 YEAR,
                    active_to         = active_to   + INTERVAL 5 YEAR
                WHERE iblock_section_id = :src_id
            """
            ),
            {
                "dest_id": destination_section_id,
                "src_id": source_section_id,
                "user": const.DEFAULT_USER_ID,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO b_iblock_section_element
                    (iblock_section_id, iblock_element_id, additional_property_id)
                SELECT :dest_id, id, NULL
                FROM b_iblock_element
                WHERE iblock_section_id = :dest_id
            """
            ),
            {"dest_id": destination_section_id},
        )

        for element_id in await DatabaseClient._get_affected_elements(
            session, destination_section_id
        ):
            await session.execute(
                text(
                    """
                    UPDATE b_iblock_element_property
                    SET value = value + :year_offset
                    WHERE iblock_element_id = :element_id
                      AND iblock_property_id = :prop_id
                """
                ),
                {
                    "year_offset": const.CHRONOGRAPH_YEAR_OFFSET,
                    "element_id": element_id,
                    "prop_id": const.CHRONOGRAPH_YEAR_PROPERTY_ID,
                },
            )

        await session.execute(
            text("UPDATE b_iblock_section SET active = 'N' WHERE id = :src_id"),
            {"src_id": source_section_id},
        )

        LOGGER.info(
            "Finished copying chronograph section %s into %s",
            source_section_id,
            destination_section_id,
        )

    @staticmethod
    async def _get_affected_elements(session: AsyncSession, section_id: int) -> list[int]:
        """Get element IDs within a section (uses the provided session to see uncommitted rows)."""
        result = await session.execute(
            text("SELECT id FROM b_iblock_element WHERE iblock_section_id = :section_id"),
            {"section_id": section_id},
        )
        return [row[0] for row in result.all()]

    @staticmethod
    async def insert_new_file(
        session: AsyncSession,
        subdir: str,
        filename: str,
        content_type: str,
        width: int,
        height: int,
        file_size: int,
    ) -> int:
        """Insert a new file record into b_file for an uploaded image.

        Args:
            session: The active database session.
            subdir: The subdirectory path where the file was uploaded.
            filename: The file name on disk.
            content_type: The MIME type (e.g. ``"image/jpeg"``).
            width: Image width in pixels.
            height: Image height in pixels.
            file_size: File size in bytes.

        Returns:
            The ID of the newly inserted file record.
        """
        await session.execute(
            text(
                """
                INSERT INTO b_file (
                    timestamp_x, module_id, height, width, file_size,
                    content_type, subdir, file_name, original_name,
                    description, handler_id, external_id
                ) VALUES (
                    NOW(), 'iblock', :height, :width, :file_size,
                    :content_type, :subdir, :file_name, :file_name,
                    NULL, NULL, NULL
                )
            """
            ),
            {
                "height": height,
                "width": width,
                "file_size": file_size,
                "content_type": content_type,
                "subdir": subdir,
                "file_name": filename,
            },
        )
        return await DatabaseClient._get_last_insert_id(session)

    @staticmethod
    async def insert_book_section(session: AsyncSession, section_name: str) -> int:
        """Insert a new top-level section in the book iblock (iblock 9).

        Args:
            session: The active database session.
            section_name: The name for the new section (typically the exhibition title).

        Returns:
            The ID of the newly inserted section.
        """
        await session.execute(
            text(
                """
                INSERT INTO b_iblock_section (
                    timestamp_x, modified_by, date_create, created_by,
                    iblock_id, iblock_section_id, active, global_active,
                    sort, name, picture, depth_level, searchable_content,
                    description_type, tmp_id, detail_picture, socnet_group_id
                ) VALUES (
                    NOW(), :user, NOW(), :user,
                    :iblock_id, NULL, 'Y', 'Y',
                    :sort, :name, NULL, 1, :searchable_content,
                    'text', 0, NULL, NULL
                )
            """
            ),
            {
                "user": const.DEFAULT_USER_ID,
                "iblock_id": const.BOOK_IBLOCK_ID,
                "sort": const.BOOK_DEFAULT_SORT,
                "name": section_name,
                "searchable_content": section_name.upper(),
            },
        )
        return await DatabaseClient._get_last_insert_id(session)

    @staticmethod
    async def insert_exhibition_element(
        session: AsyncSession,
        title: str,
        preview_text: str,
        detail_text: str,
        preview_picture_id: int,
        detail_picture_id: int,
        active_from: datetime,
    ) -> int:
        """Insert a new exhibition element in iblock 14.

        Args:
            session: The active database session.
            title: The exhibition title (used as the element name).
            preview_text: HTML preview text.
            detail_text: HTML detail text.
            preview_picture_id: File ID of the preview picture.
            detail_picture_id: File ID of the detail picture.
            active_from: The activation date/time for the element.

        Returns:
            The ID of the newly inserted element.
        """
        active_from_str = active_from.strftime(const.DATETIME_FORMAT)
        await session.execute(
            text(
                """
                INSERT INTO b_iblock_element (
                    timestamp_x, modified_by, date_create, created_by,
                    iblock_id, iblock_section_id, active, active_from, active_to,
                    sort, name, preview_picture, preview_text, preview_text_type,
                    detail_picture, detail_text, detail_text_type,
                    searchable_content, tmp_id
                ) VALUES (
                    NOW(), :user, NOW(), :user,
                    :iblock_id, NULL, 'Y', :active_from, NULL,
                    :sort, :name, :preview_picture, :preview_text, 'html',
                    :detail_picture, :detail_text, 'html',
                    :searchable_content, 0
                )
            """
            ),
            {
                "user": const.DEFAULT_USER_ID,
                "iblock_id": const.EXHIBITION_IBLOCK_ID,
                "active_from": active_from_str,
                "sort": const.EXHIBITION_DEFAULT_SORT,
                "name": title,
                "preview_picture": preview_picture_id,
                "preview_text": preview_text,
                "detail_picture": detail_picture_id,
                "detail_text": detail_text,
                "searchable_content": title.upper(),
            },
        )
        element_id = await DatabaseClient._get_last_insert_id(session)
        await session.execute(
            text("UPDATE b_iblock_element SET xml_id = :id WHERE id = :id"),
            {"id": element_id},
        )
        return element_id

    @staticmethod
    async def insert_book_element(
        session: AsyncSession,
        title: str,
        section_id: int,
        preview_text: str,
        detail_text: str,
        preview_picture_id: int,
        detail_picture_id: int,
        active_from: datetime,
        sort: int,
    ) -> int:
        """Insert a new book element in iblock 9 and link it to its section.

        Args:
            session: The active database session.
            title: The book title (used as the element name).
            section_id: The ID of the parent section.
            preview_text: HTML preview text (first sentence of description).
            detail_text: HTML full description.
            preview_picture_id: File ID of the cover used as preview picture.
            detail_picture_id: File ID of the cover used as detail picture.
            active_from: The activation date/time for the element.
            sort: Sort order.

        Returns:
            The ID of the newly inserted element.
        """
        active_from_str = active_from.strftime(const.DATETIME_FORMAT)
        await session.execute(
            text(
                """
                INSERT INTO b_iblock_element (
                    timestamp_x, modified_by, date_create, created_by,
                    iblock_id, iblock_section_id, active, active_from, active_to,
                    sort, name, preview_picture, preview_text, preview_text_type,
                    detail_picture, detail_text, detail_text_type,
                    searchable_content, tmp_id
                ) VALUES (
                    NOW(), :user, NOW(), :user,
                    :iblock_id, :section_id, 'Y', :active_from, NULL,
                    :sort, :name, :preview_picture, :preview_text, 'html',
                    :detail_picture, :detail_text, 'html',
                    :searchable_content, 0
                )
            """
            ),
            {
                "user": const.DEFAULT_USER_ID,
                "iblock_id": const.BOOK_IBLOCK_ID,
                "section_id": section_id,
                "active_from": active_from_str,
                "sort": sort,
                "name": title,
                "preview_picture": preview_picture_id,
                "preview_text": preview_text,
                "detail_picture": detail_picture_id,
                "detail_text": detail_text,
                "searchable_content": title.upper(),
            },
        )
        element_id = await DatabaseClient._get_last_insert_id(session)
        await session.execute(
            text("UPDATE b_iblock_element SET xml_id = :id WHERE id = :id"),
            {"id": element_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO b_iblock_section_element
                    (iblock_section_id, iblock_element_id, additional_property_id)
                VALUES (:section_id, :element_id, NULL)
            """
            ),
            {"section_id": section_id, "element_id": element_id},
        )
        return element_id

    @staticmethod
    async def set_exhibition_properties(
        session: AsyncSession,
        element_id: int,
        section_id: int,
        active_from: datetime,
    ) -> None:
        """Set the section-link and date properties on an exhibition element.

        Args:
            session: The active database session.
            element_id: The ID of the exhibition element.
            section_id: The ID of the linked book section.
            active_from: The activation datetime for the exhibition.
        """
        date_value = active_from.strftime("%Y-%m-%d 00:00:00")
        year_num = float(active_from.year)
        await session.execute(
            text(
                """
                INSERT INTO b_iblock_element_property
                    (iblock_property_id, iblock_element_id, value,
                     value_type, value_enum, value_num, description)
                VALUES
                    (:prop_section, :id, :section_id,
                     'text', NULL, :section_num, NULL),
                    (:prop_date,    :id, :date_value,
                     'text', NULL, :year_num,    NULL)
            """
            ),
            {
                "prop_section": const.EXHIBITION_SECTION_PROPERTY_ID,
                "prop_date": const.EXHIBITION_DATE_PROPERTY_ID,
                "id": element_id,
                "section_id": str(section_id),
                "section_num": float(section_id),
                "date_value": date_value,
                "year_num": year_num,
            },
        )

    @staticmethod
    async def set_book_properties(
        session: AsyncSession,
        book_id: int,
        full_bib_text: str,
        author: str,
        city: str,
        publisher: str,
        year: str,
    ) -> None:
        """Set bibliographic iblock properties on a book element.

        Args:
            session: The active database session.
            book_id: The ID of the book element.
            full_bib_text: PHP-serialized full bib string (property 30).
            author: Author string (property 31).
            city: City abbreviation (property 57).
            publisher: Publisher name (property 58).
            year: Publication year string (property 59).
        """
        year_num = float(year) if year.isdigit() else 0.0
        await session.execute(
            text(
                """
                INSERT INTO b_iblock_element_property
                    (iblock_property_id, iblock_element_id, value, value_type,
                     value_enum, value_num, description)
                VALUES
                    (:prop30, :id, :full_bib,  'text', NULL, 0.0,       NULL),
                    (:prop31, :id, :author,    'text', NULL, 0.0,       NULL),
                    (:prop57, :id, :city,      'text', NULL, 0.0,       NULL),
                    (:prop58, :id, :publisher, 'text', NULL, 0.0,       NULL),
                    (:prop59, :id, :year,      'text', NULL, :year_num, NULL)
            """
            ),
            {
                "prop30": const.BOOK_FULL_BIB_PROPERTY_ID,
                "prop31": const.BOOK_AUTHOR_PROPERTY_ID,
                "prop57": const.BOOK_CITY_PROPERTY_ID,
                "prop58": const.BOOK_PUBLISHER_PROPERTY_ID,
                "prop59": const.BOOK_YEAR_PROPERTY_ID,
                "id": book_id,
                "full_bib": full_bib_text,
                "author": author,
                "city": city,
                "publisher": publisher,
                "year": year,
                "year_num": year_num,
            },
        )

    @staticmethod
    async def _get_last_insert_id(session: AsyncSession) -> int:
        result = await session.execute(text("SELECT LAST_INSERT_ID()"))
        return int(result.scalar_one())

    @staticmethod
    def generate_new_subdir() -> str:
        """Generate a unique subdirectory path for a new file upload.

        Returns:
            A path string in the form ``iblock/xxx/xxx/<uuid>``.
        """
        file_hash = uuid4().hex
        return f"iblock/{file_hash[:3]}/{file_hash[3:6]}/{file_hash}"

    @staticmethod
    async def insert_virtual_exhibition_element(
        session: AsyncSession,
        title: str,
        preview_text: str,
        detail_text: str,
        preview_picture_id: int,
        detail_picture_id: int,
        active_from: datetime,
        active_to: datetime,
    ) -> int:
        """Insert a new virtual exhibition element in iblock 5.

        Args:
            session: The active database session.
            title: The exhibition title (element name).
            preview_text: HTML preview text.
            detail_text: HTML detail text.
            preview_picture_id: File ID for the preview picture.
            detail_picture_id: File ID for the detail picture.
            active_from: The start date of the exhibition.
            active_to: The end date of the exhibition (exclusive, element active_to).

        Returns:
            The ID of the newly inserted element.
        """
        active_from_str = active_from.strftime(const.DATETIME_FORMAT)
        active_to_str = active_to.strftime(const.DATETIME_FORMAT)
        await session.execute(
            text(
                """
                INSERT INTO b_iblock_element (
                    timestamp_x, modified_by, date_create, created_by,
                    iblock_id, iblock_section_id, active, active_from, active_to,
                    sort, name, preview_picture, preview_text, preview_text_type,
                    detail_picture, detail_text, detail_text_type,
                    searchable_content, tmp_id
                ) VALUES (
                    NOW(), :user, NOW(), :user,
                    :iblock_id, :section_id, 'Y', :active_from, :active_to,
                    :sort, :name, :preview_picture, :preview_text, 'html',
                    :detail_picture, :detail_text, 'html',
                    :searchable_content, 0
                )
            """
            ),
            {
                "user": const.DEFAULT_USER_ID,
                "iblock_id": const.VIRTUAL_EXHIBITION_IBLOCK_ID,
                "section_id": const.VIRTUAL_EXHIBITION_IBLOCK_SECTION_ID,
                "active_from": active_from_str,
                "active_to": active_to_str,
                "sort": const.VIRTUAL_EXHIBITION_DEFAULT_SORT,
                "name": title,
                "preview_picture": preview_picture_id,
                "preview_text": preview_text,
                "detail_picture": detail_picture_id,
                "detail_text": detail_text,
                "searchable_content": title.upper(),
            },
        )
        element_id = await DatabaseClient._get_last_insert_id(session)
        await session.execute(
            text("UPDATE b_iblock_element SET xml_id = :id WHERE id = :id"),
            {"id": element_id},
        )
        return element_id

    @staticmethod
    async def set_virtual_exhibition_properties(
        session: AsyncSession,
        element_id: int,
        subtitle: str,
        active_from: datetime,
        active_to: datetime,
    ) -> None:
        """Set fixed and date properties on a virtual exhibition element.

        Inserts props 9 (type), 54 (active_from), 55 (active_to), 66 (sort=0),
        196 (subtitle), and 213 (category link).

        Args:
            session: The active database session.
            element_id: The ID of the virtual exhibition element.
            subtitle: The subtitle text (prop 196).
            active_from: The start date for props 54.
            active_to: The display end date for prop 55 (one day before element active_to).
        """
        from_str = active_from.strftime("%Y-%m-%d 00:00:00")
        to_str = active_to.strftime("%Y-%m-%d 00:00:00")
        from_year = float(active_from.year)
        to_year = float(active_to.year)

        await session.execute(
            text(
                """
                INSERT INTO b_iblock_element_property
                    (iblock_property_id, iblock_element_id, value,
                     value_type, value_enum, value_num, description)
                VALUES
                    (:prop9,   :id, :val9,     'text', :enum9,   NULL,      NULL),
                    (:prop54,  :id, :from_str, 'text', NULL,     :from_year, NULL),
                    (:prop55,  :id, :to_str,   'text', NULL,     :to_year,   NULL),
                    (:prop66,  :id, '0',       'text', NULL,     0.0,        NULL),
                    (:prop196, :id, :subtitle, 'text', NULL,     0.0,        NULL),
                    (:prop213, :id, :val213,   'text', :enum213, NULL,       NULL)
            """
            ),
            {
                "prop9": const.VIRTUAL_EXHIBITION_PROP_TYPE_ID,
                "val9": str(const.VIRTUAL_EXHIBITION_PROP_TYPE_VALUE),
                "enum9": const.VIRTUAL_EXHIBITION_PROP_TYPE_VALUE,
                "prop54": const.VIRTUAL_EXHIBITION_PROP_ACTIVE_FROM_ID,
                "from_str": from_str,
                "from_year": from_year,
                "prop55": const.VIRTUAL_EXHIBITION_PROP_ACTIVE_TO_ID,
                "to_str": to_str,
                "to_year": to_year,
                "prop66": const.VIRTUAL_EXHIBITION_PROP_SORT_ID,
                "prop196": const.VIRTUAL_EXHIBITION_PROP_SUBTITLE_ID,
                "subtitle": subtitle,
                "prop213": const.VIRTUAL_EXHIBITION_PROP_CATEGORY_ID,
                "val213": str(const.VIRTUAL_EXHIBITION_PROP_CATEGORY_VALUE),
                "enum213": const.VIRTUAL_EXHIBITION_PROP_CATEGORY_VALUE,
                "id": element_id,
            },
        )

    @staticmethod
    async def insert_virtual_exhibition_item(
        session: AsyncSession,
        exhibition_id: int,
        name: str,
        bib_html: str,
        description_html: str,
        image_file_ids: list[int],
    ) -> None:
        """Insert all properties for one virtual exhibition item.

        Inserts props 197 (name), 198 (bib), 199 (description), 200 (image file IDs),
        and 205 (linking array), all sharing the same ``scp_<id>`` description key.

        The scp key is derived from the LAST_INSERT_ID after inserting prop 197.

        Args:
            session: The active database session.
            exhibition_id: The ID of the parent virtual exhibition element.
            name: The item title (prop 197).
            bib_html: PHP-serialised bib HTML (prop 198).
            description_html: PHP-serialised description HTML (prop 199).
            image_file_ids: File IDs for the item images (prop 200, one row per image).
        """
        # Insert prop 197 (name) and derive the scp key from its ID
        await session.execute(
            text(
                """
                INSERT INTO b_iblock_element_property
                    (iblock_property_id, iblock_element_id, value,
                     value_type, value_enum, value_num, description)
                VALUES (:prop197, :eid, :name, 'text', NULL, 0.0, NULL)
            """
            ),
            {
                "prop197": const.VIRTUAL_EXHIBITION_PROP_ITEM_NAME_ID,
                "eid": exhibition_id,
                "name": name,
            },
        )
        prop197_id = await DatabaseClient._get_last_insert_id(session)
        scp_key = f"scp_{prop197_id}"

        # Update the scp key on prop 197
        await session.execute(
            text("UPDATE b_iblock_element_property SET description = :scp WHERE id = :pid"),
            {"scp": scp_key, "pid": prop197_id},
        )

        # Insert prop 198 (bib info)
        await session.execute(
            text(
                """
                INSERT INTO b_iblock_element_property
                    (iblock_property_id, iblock_element_id, value,
                     value_type, value_enum, value_num, description)
                VALUES (:prop198, :eid, :bib, 'text', NULL, 0.0, :scp)
            """
            ),
            {
                "prop198": const.VIRTUAL_EXHIBITION_PROP_ITEM_BIB_ID,
                "eid": exhibition_id,
                "bib": bib_html,
                "scp": scp_key,
            },
        )
        prop198_id = await DatabaseClient._get_last_insert_id(session)

        # Insert prop 199 (description)
        await session.execute(
            text(
                """
                INSERT INTO b_iblock_element_property
                    (iblock_property_id, iblock_element_id, value,
                     value_type, value_enum, value_num, description)
                VALUES (:prop199, :eid, :desc, 'text', NULL, 0.0, :scp)
            """
            ),
            {
                "prop199": const.VIRTUAL_EXHIBITION_PROP_ITEM_DESC_ID,
                "eid": exhibition_id,
                "desc": description_html,
                "scp": scp_key,
            },
        )
        prop199_id = await DatabaseClient._get_last_insert_id(session)

        # Insert prop 200 (image file IDs — one row per image)
        prop200_ids: list[int] = []
        for file_id in image_file_ids:
            await session.execute(
                text(
                    """
                    INSERT INTO b_iblock_element_property
                        (iblock_property_id, iblock_element_id, value,
                         value_type, value_enum, value_num, description)
                    VALUES (:prop200, :eid, :fid, 'text', NULL, :fnum, :scp)
                """
                ),
                {
                    "prop200": const.VIRTUAL_EXHIBITION_PROP_ITEM_IMAGE_ID,
                    "eid": exhibition_id,
                    "fid": str(file_id),
                    "fnum": float(file_id),
                    "scp": scp_key,
                },
            )
            prop200_ids.append(await DatabaseClient._get_last_insert_id(session))

        # Build and insert prop 205 (linking array)
        link_value = _php_serialize_item_link(
            exhibition_id=exhibition_id,
            scp_key=scp_key,
            prop197_id=prop197_id,
            prop198_id=prop198_id,
            prop199_id=prop199_id,
            prop200_ids=prop200_ids,
            image_file_ids=image_file_ids,
        )
        await session.execute(
            text(
                """
                INSERT INTO b_iblock_element_property
                    (iblock_property_id, iblock_element_id, value,
                     value_type, value_enum, value_num, description)
                VALUES (:prop205, :eid, :val, 'text', 0, 0.0, :scp)
            """
            ),
            {
                "prop205": const.VIRTUAL_EXHIBITION_PROP_ITEM_LINK_ID,
                "eid": exhibition_id,
                "val": link_value,
                "scp": scp_key,
            },
        )


def _php_serialize_item_link(
    exhibition_id: int,
    scp_key: str,
    prop197_id: int,
    prop198_id: int,
    prop199_id: int,
    prop200_ids: list[int],
    image_file_ids: list[int],
) -> str:
    """Build the PHP-serialised prop 205 linking array for one virtual exhibition item.

    The format mirrors what Bitrix CMS stores for composite property groups::

        a:1:{i:<elem_id>;a:4:{i:197;a:1:{i:<id>;s:<len>:"scp_X";}...}}

    For prop 200 the inner value is the file ID string, not the scp key.
    """
    scp_bytes = scp_key.encode("utf-8")
    scp_len = len(scp_bytes)
    scp_s = f's:{scp_len}:"{scp_key}"'

    def _prop_entry_scp(prop_id: int, record_ids: list[int]) -> str:
        inner = "".join(f"i:{rid};{scp_s};" for rid in record_ids)
        return f"i:{prop_id};a:{len(record_ids)}:{{{inner}}}"

    def _prop_entry_file(prop_id: int, row_ids: list[int], file_ids: list[int]) -> str:
        parts = []
        for row_id, file_id in zip(row_ids, file_ids):
            fid_str = str(file_id)
            parts.append(f'i:{row_id};s:{len(fid_str)}:"{fid_str}";')
        return f"i:{prop_id};a:{len(row_ids)}:{{{''.join(parts)}}}"

    entries = [
        _prop_entry_scp(197, [prop197_id]),
        _prop_entry_scp(198, [prop198_id]),
        _prop_entry_scp(199, [prop199_id]),
        _prop_entry_file(200, prop200_ids, image_file_ids),
    ]
    inner = "".join(entries)
    return f"a:1:{{i:{exhibition_id};a:{len(entries)}:{{{inner}}}}}"
