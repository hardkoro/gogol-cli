"""Clients."""

import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from itertools import count
from typing import TypedDict
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gogol_cli import constants as const
from gogol_cli.exceptions import DBEventNotFoundError
from gogol_cli.schemas import Event, File

LOGGER = logging.getLogger(__name__)


def _strip_html(s: str | None) -> str:
    return re.sub(r"<[^>]+>", " ", s or "")


# Matches Bitrix's default transliteration table (CUtil::translit), used to build
# the news element CODE that its detail page URL (#ELEMENT_CODE#) depends on.
_TRANSLIT_TABLE = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _transliterate(text: str) -> str:
    """Transliterate Cyrillic characters to Latin, preserving case."""
    result = []
    for ch in text:
        lower = ch.lower()
        mapped = _TRANSLIT_TABLE.get(lower)
        if mapped is None:
            result.append(ch)
            continue
        if ch.isupper() and mapped:
            mapped = mapped[0].upper() + mapped[1:]
        result.append(mapped)
    return "".join(result)


def _slugify_title(title: str) -> str:
    """Build a Bitrix-style element CODE from *title* (transliterate, strip non-alnum)."""
    transliterated = _transliterate(title)
    return re.sub(r"[^0-9A-Za-z]", "", transliterated)


class NewEventProperties(TypedDict):
    """Property payload for creating a new event."""

    purchase_link: str | None
    registration_link: str | None
    description_buy_ticket: str
    phone: str
    email: str
    address: str
    location_id: int
    type_of_activity_id: int


class DatabaseClient:
    """Database client."""

    def __init__(self, database_uri: str) -> None:
        """Initialize the client.

        Args:
            database_uri: Database URI.
        """
        # pool_pre_ping: validate a pooled connection with a cheap SELECT 1 before
        # handing it out, so a connection dropped by the (flaky, shared-hosting) MySQL
        # server between statements gets discarded instead of being reused as-is on
        # the next checkout — otherwise a retry can be handed back the same dead
        # connection and fail identically every time.
        self._engine = create_async_engine(database_uri, echo=False, pool_pre_ping=True)
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
                text("""
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
                """),
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
            text("""
                INSERT INTO b_file (
                    timestamp_x, module_id, height, width, file_size, content_type,
                    subdir, file_name, original_name, description, handler_id, external_id
                )
                SELECT
                    timestamp_x, module_id, height, width, file_size, content_type,
                    :subdir, file_name, original_name, description, handler_id, external_id
                FROM b_file
                WHERE id = :original_id
            """),
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
            text("""
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
            """),
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
            text("""
                INSERT INTO b_iblock_element_property
                    (iblock_property_id, iblock_element_id, value, value_type, value_num)
                VALUES
                    (:link_prop_id,  :pin_id, :url,        'text', 0.0000),
                    (:btn_prop_id,   :pin_id, 'Подробнее', 'text', 0.0000),
                    (:name_prop_id,  :pin_id, :name,       'text', 0.0000)
            """),
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
    ) -> tuple[int, str, str]:
        """Insert a copy of an event element with a new date and time.

        Args:
            session: The active database session.
            event: The source event to copy.
            preview_picture_id: The file ID to use as the new event's preview picture.
            detail_picture_id: The file ID to use as the new event's detail picture.
            new_event_date: The date of the new event.
            new_event_time: The time of the new event in HH-MM format.

        Returns:
            A tuple of (new event element ID, active_from, active_to).
        """
        now = datetime.now(tz=None).strftime(const.DATETIME_FORMAT)
        hours, minutes = new_event_time.split("-")
        active_from = now
        active_to = (
            new_event_date + timedelta(hours=int(hours) + 1, minutes=int(minutes))
        ).strftime(const.DATETIME_FORMAT)

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
            text("""
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
            """),
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

        return new_event_id, active_from, active_to

    @staticmethod
    async def insert_search_content(
        session: AsyncSession,
        event_id: int,
        title: str,
        preview_text: str | None,
        detail_text: str | None,
        tags: str | None,
        date_from: str,
        date_to: str,
    ) -> None:
        """Insert a b_search_content row so the calendar filter picks up an event.

        Args:
            session: The active database session.
            event_id: The ID of the event element (also used as the search item ID).
            title: The event name.
            preview_text: The event's preview HTML text.
            detail_text: The event's detail HTML text.
            tags: The event's tags.
            date_from: The event's active-from timestamp, formatted per ``const.DATETIME_FORMAT``.
            date_to: The event's active-to timestamp, formatted per ``const.DATETIME_FORMAT``.
        """
        now = datetime.now(tz=None).strftime(const.DATETIME_FORMAT)
        url = (
            f"=ID={event_id}&EXTERNAL_ID={event_id}"
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
                    _strip_html(preview_text),
                    _strip_html(detail_text),
                ],
            )
        )
        await session.execute(
            text("""
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
            """),
            {
                "now": now,
                "item_id": str(event_id),
                "url": url,
                "title": title,
                "body": body,
                "tags": tags,
                "param1": const.EVENT_IBLOCK_TYPE_ID,
                "param2": str(const.EVENT_IBLOCK_ID),
                "date_from": date_from,
                "date_to": date_to,
            },
        )

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
            text("""
                INSERT INTO b_iblock_element_property
                    (iblock_property_id, iblock_element_id, value, value_type, value_enum, value_num, description)
                SELECT iblock_property_id, :new_id, value, value_type, value_enum, value_num, description
                FROM b_iblock_element_property
                WHERE iblock_element_id = :old_id
            """),
            {"new_id": new_event_id, "old_id": old_event.id},
        )
        await session.execute(
            text("""
                UPDATE b_iblock_element_property
                SET value = :time_value
                WHERE iblock_element_id = :event_id
                  AND iblock_property_id = :prop_id
            """),
            {
                "time_value": new_event_time.replace("-", ":"),
                "event_id": new_event_id,
                "prop_id": const.EVENT_TIME_PROPERTY_ID,
            },
        )
        await session.execute(
            text("""
                UPDATE b_iblock_element_property
                SET value = :date_value
                WHERE iblock_element_id = :event_id
                  AND iblock_property_id = :prop_id
            """),
            {
                "date_value": new_event_date.strftime(const.DATETIME_FORMAT),
                "event_id": new_event_id,
                "prop_id": const.EVENT_DATE_PROPERTY_ID,
            },
        )
        if new_event_price is not None:
            await session.execute(
                text("""
                    UPDATE b_iblock_element_property
                    SET value = :price
                    WHERE iblock_element_id = :event_id
                      AND iblock_property_id = :prop_id
                """),
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
            text("""
                INSERT INTO b_iblock_section_element
                    (iblock_section_id, iblock_element_id, additional_property_id)
                VALUES (:section_id, :element_id, NULL)
            """),
            {"section_id": section_id, "element_id": element_id},
        )
        # Bitrix's own CIBlockElement::Add() sets this flag whenever an element
        # gets a b_iblock_section_element row; leaving it at the column default
        # ('N') makes the admin's "Sections" checkbox tree render this element's
        # section as unchecked, which then wipes it on the next save.
        await session.execute(
            text("UPDATE b_iblock_element SET in_sections = 'Y' WHERE id = :element_id"),
            {"element_id": element_id},
        )

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
            text("""
                INSERT INTO b_file (
                    timestamp_x, module_id, height, width, file_size, content_type,
                    subdir, file_name, original_name, description, handler_id, external_id
                )
                VALUES (
                    NOW(), 'iblock', :height, :width, :file_size, :content_type,
                    :subdir, :file_name, :file_name, NULL, NULL, NULL
                )
            """),
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
    async def insert_new_file_with_subdir(
        session: AsyncSession,
        subdir: str,
        file_name: str,
        file_size: int,
        height: int = 0,
        width: int = 0,
    ) -> int:
        """Insert a new b_file record with a pre-generated subdir (for SSH uploads).

        Args:
            session: The active database session.
            subdir: The subdirectory path where the file was uploaded via SSH.
            file_name: The original file name.
            file_size: The size of the file in bytes.
            height: Image height (0 if unknown).
            width: Image width (0 if unknown).

        Returns:
            The ID of the newly inserted file record.
        """
        content_type = "image/png" if file_name.endswith(".png") else "image/jpeg"

        await session.execute(
            text("""
                INSERT INTO b_file (
                    timestamp_x, module_id, height, width, file_size, content_type,
                    subdir, file_name, original_name, description, handler_id, external_id
                )
                VALUES (
                    :now, 'iblock', :height, :width, :file_size, :content_type,
                    :subdir, :file_name, :original_name, '', 'default', ''
                )
            """),
            {
                "now": datetime.now(tz=None).strftime(const.DATETIME_FORMAT),
                "height": height,
                "width": width,
                "file_size": file_size,
                "content_type": content_type,
                "subdir": subdir,
                "file_name": file_name,
                "original_name": file_name,
            },
        )
        return await DatabaseClient._get_last_insert_id(session)

    @staticmethod
    async def insert_new_event(
        session: AsyncSession,
        name: str,
        event_date_time: datetime,
        preview_picture_id: int | None,
        detail_picture_id: int | None,
        preview_text: str,
        detail_text: str,
        tags: str | None = None,
        is_active: bool = True,
    ) -> tuple[int, str, str]:
        """Insert a completely new event element.

        Args:
            session: The active database session.
            name: The event name.
            event_date_time: The date and time of the event.
            preview_picture_id: The file ID for the preview picture, or None.
            detail_picture_id: The file ID for the detail picture, or None.
            preview_text: The preview text (typically HTML).
            detail_text: The detail text (typically HTML).
            tags: Optional tags for the event.
            is_active: Whether the event should be active (default: True).

        Returns:
            A tuple of (new event element ID, active_from, active_to).
        """
        now = datetime.now(tz=None).strftime(const.DATETIME_FORMAT)

        # Calculate active_to: 1 hour after the event time
        active_to = (event_date_time + timedelta(hours=1)).strftime(const.DATETIME_FORMAT)

        searchable_content = " ".join(
            filter(
                None,
                [
                    name,
                    _strip_html(preview_text),
                    _strip_html(detail_text),
                ],
            )
        ).upper()

        active_status = "Y" if is_active else "N"

        await session.execute(
            text("""
                INSERT INTO b_iblock_element (
                    timestamp_x, modified_by, date_create, created_by,
                    iblock_id, iblock_section_id, active, active_from, active_to,
                    sort, name, preview_picture, preview_text, preview_text_type,
                    detail_picture, detail_text, detail_text_type,
                    searchable_content, tags, tmp_id, code
                )
                VALUES (
                    :now, :user, :now, :user,
                    :iblock_id, NULL, :active, :active_from, :active_to,
                    :sort, :name, :preview_picture, :preview_text, :preview_text_type,
                    :detail_picture, :detail_text, :detail_text_type,
                    :searchable_content, :tags, 0, ''
                )
            """),
            {
                "now": now,
                "user": const.DEFAULT_USER_ID,
                "iblock_id": const.EVENT_IBLOCK_ID,
                "active": active_status,
                "active_from": now,
                "active_to": active_to,
                "sort": const.EVENT_DEFAULT_SORT,
                "name": name,
                "preview_picture": preview_picture_id,
                "preview_text": preview_text,
                "preview_text_type": "html",
                "detail_picture": detail_picture_id,
                "detail_text": detail_text,
                "detail_text_type": "html",
                "searchable_content": searchable_content,
                "tags": tags,
            },
        )
        new_event_id = await DatabaseClient._get_last_insert_id(session)

        return new_event_id, now, active_to

    @staticmethod
    async def set_new_event_properties(
        session: AsyncSession,
        event_id: int,
        event_date: datetime,
        event_time_str: str,
        price: str,
        properties: NewEventProperties,
    ) -> None:
        """Set properties on a newly created event.

        Args:
            session: The active database session.
            event_id: The ID of the event element.
            event_date: The event date.
            event_time_str: The event time in HH-MM format.
            price: The ticket price.
            properties: Derived property values:
                purchase_link, registration_link, description_buy_ticket,
                phone, email, address, location_id, type_of_activity_id.
        """
        purchase_link = properties["purchase_link"]
        registration_link = properties["registration_link"]
        description_buy_ticket = properties["description_buy_ticket"]
        phone = properties["phone"]
        email = properties["email"]
        address = properties["address"]
        location_id = properties["location_id"]
        type_of_activity_id = properties["type_of_activity_id"]

        await session.execute(
            text("UPDATE b_iblock_element SET xml_id = :event_id WHERE id = :event_id"),
            {"event_id": event_id},
        )

        # Insert event properties
        await session.execute(
            text("""
                INSERT INTO b_iblock_element_property
                    (iblock_property_id, iblock_element_id, value, value_type, value_enum, value_num, description)
                VALUES
                    (:date_prop_id, :event_id, :date_value, 'text', NULL, 0.0000, ''),
                    (:time_prop_id, :event_id, :time_value, 'text', NULL, 0.0000, ''),
                    (:price_prop_id, :event_id, :price_value, 'text', NULL, 0.0000, ''),
                    (:purchase_link_prop_id, :event_id, :purchase_link_value, 'text', NULL, 0.0000, ''),
                    (:registration_link_prop_id, :event_id, :registration_link_value, 'text', NULL, 0.0000, ''),
                    (:desc_buy_ticket_prop_id, :event_id, :desc_buy_ticket_value, 'text', NULL, 0.0000, ''),
                    (:phone_prop_id, :event_id, :phone_value, 'text', NULL, 0.0000, ''),
                    (:email_prop_id, :event_id, :email_value, 'text', NULL, 0.0000, ''),
                    (:address_prop_id, :event_id, :address_value, 'text', NULL, 0.0000, ''),
                    (:location_prop_id, :event_id, :location_value, 'text', :location_enum, 0.0000, ''),
                    (:type_of_activity_prop_id, :event_id, :type_of_activity_value, 'text', :type_of_activity_enum, 0.0000, '')
            """),
            {
                "date_prop_id": const.EVENT_DATE_PROPERTY_ID,
                "time_prop_id": const.EVENT_TIME_PROPERTY_ID,
                "price_prop_id": const.EVENT_PRICE_PROPERTY_ID,
                "purchase_link_prop_id": const.EVENT_PURCHASE_LINK_PROPERTY_ID,
                "registration_link_prop_id": const.EVENT_REGISTRATION_LINK_PROPERTY_ID,
                "desc_buy_ticket_prop_id": const.EVENT_DESCRIPTION_BUY_TICKET_PROPERTY_ID,
                "phone_prop_id": const.EVENT_PHONE_PROPERTY_ID,
                "email_prop_id": const.EVENT_EMAIL_PROPERTY_ID,
                "address_prop_id": const.EVENT_ADDRESS_PROPERTY_ID,
                "location_prop_id": const.EVENT_LOCATION_PROPERTY_ID,
                "type_of_activity_prop_id": const.EVENT_TYPE_OF_ACTIVITY_PROPERTY_ID,
                "event_id": event_id,
                "date_value": event_date.strftime(const.DATE_FORMAT),
                "time_value": event_time_str.replace("-", ":"),
                "price_value": price,
                "purchase_link_value": purchase_link,
                "registration_link_value": registration_link,
                "desc_buy_ticket_value": description_buy_ticket,
                "phone_value": phone,
                "email_value": email,
                "address_value": address,
                "location_value": str(location_id),
                "location_enum": location_id,
                "type_of_activity_value": str(type_of_activity_id),
                "type_of_activity_enum": type_of_activity_id,
            },
        )

    async def export_statistics(
        self, start_date: datetime, end_date: datetime
    ) -> list[dict[str, int]]:
        """Query activity statistics for a given date range."""
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
        """Insert a new chronograph section with the given name."""
        LOGGER.info("Adding chronograph section %s ...", section_name)

        await session.execute(
            text("""
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
            """),
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
        """Get chronograph section ID by name."""
        result = await session.execute(
            text("""
                SELECT id
                FROM b_iblock_section
                WHERE iblock_id = :iblock_id
                  AND name = :name
                LIMIT 1
            """),
            {"iblock_id": const.CHRONOGRAPH_IBLOCK_ID, "name": section_name},
        )
        row = result.fetchone()
        if row is None:
            raise ValueError(f"Chronograph section not found: {section_name}")
        return int(row[0])

    @staticmethod
    async def copy_chronograph_section(
        session: AsyncSession,
        source_section_id: int,
        destination_section_id: int,
        active_from: datetime,
        active_to: datetime,
        copy_picture: Callable[[AsyncSession, int], Awaitable[int]],
    ) -> None:
        """Copy chronograph section elements, their pictures, properties, and shift the year.

        Args:
            session: The active database session.
            source_section_id: The section to copy elements from.
            destination_section_id: The section to copy elements into.
            active_from: The active-from date to set on every copied element.
            active_to: The active-to date to set on every copied element.
            copy_picture: Async callback that duplicates a b_file record (and its physical
                file) and returns the new file ID.
        """
        LOGGER.info(
            "Copying chronograph section %s into %s ...",
            source_section_id,
            destination_section_id,
        )

        active_from_str = active_from.strftime(const.DATETIME_FORMAT)
        active_to_str = active_to.strftime(const.DATETIME_FORMAT)

        for source_element_id in await DatabaseClient._get_affected_elements(
            session, source_section_id
        ):
            pictures_result = await session.execute(
                text("""
                    SELECT preview_picture, detail_picture
                    FROM b_iblock_element
                    WHERE id = :src_element_id
                """),
                {"src_element_id": source_element_id},
            )
            pictures_row = pictures_result.fetchone()
            assert pictures_row is not None
            old_preview_picture, old_detail_picture = pictures_row

            new_preview_picture = await copy_picture(session, old_preview_picture)
            new_detail_picture = await copy_picture(session, old_detail_picture)

            await session.execute(
                text("""
                    INSERT INTO b_iblock_element (
                        timestamp_x, modified_by, date_create, created_by,
                        iblock_id, iblock_section_id, active, active_from, active_to,
                        sort, name, preview_picture, preview_text, preview_text_type,
                        detail_picture, detail_text, detail_text_type,
                        searchable_content, tmp_id, code
                    )
                    SELECT
                        NOW(), modified_by, NOW(), created_by,
                        iblock_id, :dst_section_id, 'Y', :active_from, :active_to,
                        sort, name, :new_preview_picture, preview_text, preview_text_type,
                        :new_detail_picture, detail_text, detail_text_type,
                        searchable_content, 0, code
                    FROM b_iblock_element
                    WHERE id = :src_element_id
                """),
                {
                    "src_element_id": source_element_id,
                    "dst_section_id": destination_section_id,
                    "active_from": active_from_str,
                    "active_to": active_to_str,
                    "new_preview_picture": new_preview_picture,
                    "new_detail_picture": new_detail_picture,
                },
            )
            new_element_id = await DatabaseClient._get_last_insert_id(session)

            await session.execute(
                text("UPDATE b_iblock_element SET xml_id = :new_id WHERE id = :new_id"),
                {"new_id": new_element_id},
            )

            await DatabaseClient.add_element_to_section(
                session, new_element_id, destination_section_id
            )

            await session.execute(
                text("""
                    INSERT INTO b_iblock_element_property
                        (iblock_property_id, iblock_element_id, value, value_type, value_enum, value_num, description)
                    SELECT iblock_property_id, :new_id, value, value_type, value_enum, value_num, description
                    FROM b_iblock_element_property
                    WHERE iblock_element_id = :old_id
                """),
                {"new_id": new_element_id, "old_id": source_element_id},
            )

            await session.execute(
                text("""
                    UPDATE b_iblock_element_property
                    SET value = value + :year_offset
                    WHERE iblock_element_id = :element_id
                      AND iblock_property_id = :prop_id
                """),
                {
                    "year_offset": const.CHRONOGRAPH_YEAR_OFFSET,
                    "element_id": new_element_id,
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
        """Get element IDs within a section (including uncommitted rows)."""
        result = await session.execute(
            text("SELECT id FROM b_iblock_element WHERE iblock_section_id = :section_id"),
            {"section_id": section_id},
        )
        return [int(row[0]) for row in result.all()]

    @staticmethod
    async def insert_book_section(session: AsyncSession, section_name: str) -> int:
        """Insert a new top-level section in the book iblock (iblock 9)."""
        await session.execute(
            text("""
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
            """),
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
        """Insert a new exhibition element in iblock 14."""
        active_from_str = active_from.strftime(const.DATETIME_FORMAT)
        await session.execute(
            text("""
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
            """),
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
        preview_picture_id: int | None,
        detail_picture_id: int | None,
        active_from: datetime,
        sort: int,
    ) -> int:
        """Insert a new book element in iblock 9 and link it to its section."""
        active_from_str = active_from.strftime(const.DATETIME_FORMAT)
        await session.execute(
            text("""
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
            """),
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
        await DatabaseClient.add_element_to_section(session, element_id, section_id)
        return element_id

    @staticmethod
    async def set_exhibition_properties(
        session: AsyncSession,
        element_id: int,
        section_id: int,
        active_from: datetime,
    ) -> None:
        """Set the section-link and date properties on an exhibition element."""
        date_value = active_from.strftime("%Y-%m-%d 00:00:00")
        year_num = float(active_from.year)
        await session.execute(
            text("""
                INSERT INTO b_iblock_element_property
                    (iblock_property_id, iblock_element_id, value,
                     value_type, value_enum, value_num, description)
                VALUES
                    (:prop_section, :id, :section_id,
                     'text', NULL, :section_num, NULL),
                    (:prop_date,    :id, :date_value,
                     'text', NULL, :year_num,    NULL)
            """),
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
        """Set bibliographic iblock properties on a book element."""
        year_num = float(year) if year.isdigit() else 0.0
        await session.execute(
            text("""
                INSERT INTO b_iblock_element_property
                    (iblock_property_id, iblock_element_id, value, value_type,
                     value_enum, value_num, description)
                VALUES
                    (:prop30, :id, :full_bib,  'text', NULL, 0.0,       NULL),
                    (:prop31, :id, :author,    'text', NULL, 0.0,       NULL),
                    (:prop57, :id, :city,      'text', NULL, 0.0,       NULL),
                    (:prop58, :id, :publisher, 'text', NULL, 0.0,       NULL),
                    (:prop59, :id, :year,      'text', NULL, :year_num, NULL)
            """),
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
    async def generate_unique_news_code(session: AsyncSession, title: str) -> str:
        """Build a Bitrix-style CODE from *title*, disambiguating against existing codes."""
        base_code = _slugify_title(title) or "News"
        code = base_code
        suffix = 2
        while await DatabaseClient.news_code_exists(session, code):
            code = f"{base_code}_{suffix}"
            suffix += 1
        return code

    @staticmethod
    async def news_code_exists(session: AsyncSession, code: str) -> bool:
        """Return True if a news element (iblock 1) already uses *code*."""
        result = await session.execute(
            text("""
                SELECT 1 FROM b_iblock_element
                WHERE iblock_id = :iblock_id AND code = :code
                LIMIT 1
            """),
            {"iblock_id": const.NEWS_IBLOCK_ID, "code": code},
        )
        return result.fetchone() is not None

    @staticmethod
    async def insert_news_element(
        session: AsyncSession,
        title: str,
        code: str,
        preview_text: str,
        detail_text: str,
        preview_picture_id: int | None,
        tags: str,
        active_from: datetime,
        sort: int,
    ) -> int:
        """Insert a new news element in iblock 1."""
        active_from_str = active_from.strftime(const.DATETIME_FORMAT)
        await session.execute(
            text("""
                INSERT INTO b_iblock_element (
                    timestamp_x, modified_by, date_create, created_by,
                    iblock_id, iblock_section_id, active, active_from, active_to,
                    sort, name, code, preview_picture, preview_text, preview_text_type,
                    detail_picture, detail_text, detail_text_type,
                    searchable_content, tags, tmp_id
                ) VALUES (
                    NOW(), :user, NOW(), :user,
                    :iblock_id, NULL, 'Y', :active_from, NULL,
                    :sort, :name, :code, :preview_picture, :preview_text, 'html',
                    NULL, :detail_text, 'html',
                    :searchable_content, :tags, 0
                )
            """),
            {
                "user": const.DEFAULT_USER_ID,
                "iblock_id": const.NEWS_IBLOCK_ID,
                "active_from": active_from_str,
                "sort": sort,
                "name": title,
                "code": code,
                "preview_picture": preview_picture_id,
                "preview_text": preview_text,
                "detail_text": detail_text,
                "searchable_content": title.upper(),
                "tags": tags,
            },
        )
        element_id = await DatabaseClient._get_last_insert_id(session)
        await session.execute(
            text("UPDATE b_iblock_element SET xml_id = :id WHERE id = :id"),
            {"id": element_id},
        )
        return element_id

    @staticmethod
    async def insert_new_files_batch(
        session: AsyncSession,
        files: list[tuple[str, str, str, int, int, int]],
    ) -> list[int]:
        """Insert several b_file rows in one round trip.

        Args:
            session: The active database session.
            files: (subdir, filename, content_type, width, height, file_size) tuples.

        Returns:
            The new file IDs, in the same order as *files*. Relies on the multi-row
            INSERT producing a contiguous auto_increment run, which holds for a single
            statement with no concurrent writers to b_file in between.
        """
        if not files:
            return []

        rows_sql = ", ".join(
            f"(NOW(), 'iblock', :height{i}, :width{i}, :file_size{i}, :content_type{i},"
            f" :subdir{i}, :file_name{i}, :file_name{i}, NULL, NULL, NULL)"
            for i in range(len(files))
        )
        params: dict[str, object] = {}
        for i, (subdir, filename, content_type, width, height, file_size) in enumerate(files):
            params[f"height{i}"] = height
            params[f"width{i}"] = width
            params[f"file_size{i}"] = file_size
            params[f"content_type{i}"] = content_type
            params[f"subdir{i}"] = subdir
            params[f"file_name{i}"] = filename

        await session.execute(
            text(f"""
                INSERT INTO b_file (
                    timestamp_x, module_id, height, width, file_size, content_type,
                    subdir, file_name, original_name, description, handler_id, external_id
                )
                VALUES {rows_sql}
            """),
            params,
        )
        first_id = await DatabaseClient._get_last_insert_id(session)
        return list(range(first_id, first_id + len(files)))

    @staticmethod
    async def insert_news_gallery_images(
        session: AsyncSession,
        element_id: int,
        file_ids: list[int],
    ) -> None:
        """Add file IDs to a news element's "Галерея" (MORE_PHOTO) gallery property."""
        if not file_ids:
            return

        rows_sql = ", ".join(
            f"(:prop_id, :element_id, :file_id{i}, 'text', :file_num{i})"
            for i in range(len(file_ids))
        )
        params: dict[str, object] = {
            "prop_id": const.NEWS_GALLERY_PROPERTY_ID,
            "element_id": element_id,
        }
        for i, file_id in enumerate(file_ids):
            params[f"file_id{i}"] = str(file_id)
            params[f"file_num{i}"] = float(file_id)

        await session.execute(
            text(f"""
                INSERT INTO b_iblock_element_property
                    (iblock_property_id, iblock_element_id, value, value_type, value_num)
                VALUES {rows_sql}
            """),
            params,
        )

    @staticmethod
    async def _get_last_insert_id(session: AsyncSession) -> int:
        result = await session.execute(text("SELECT LAST_INSERT_ID()"))
        return int(result.scalar_one())

    @staticmethod
    def generate_new_subdir() -> str:
        """Generate a unique subdirectory path for a new file upload."""
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
        """Insert a new virtual exhibition element in iblock 5."""
        active_from_str = active_from.strftime(const.DATETIME_FORMAT)
        active_to_str = active_to.strftime(const.DATETIME_FORMAT)
        await session.execute(
            text("""
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
            """),
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
        """Set fixed and date properties on a virtual exhibition element."""
        from_str = active_from.strftime("%Y-%m-%d 00:00:00")
        to_str = active_to.strftime("%Y-%m-%d 00:00:00")
        from_year = float(active_from.year)
        to_year = float(active_to.year)

        await session.execute(
            text("""
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
            """),
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
        """Insert all properties for one virtual exhibition item."""
        _lid = DatabaseClient._get_last_insert_id

        # 1. Insert name — its row ID becomes the scp anchor.
        await session.execute(
            text("""
                INSERT INTO b_iblock_element_property
                    (iblock_property_id, iblock_element_id, value, value_type, value_num)
                VALUES
                    (:prop_name, :exh_id, :name, 'text', 0.0)
            """),
            {
                "prop_name": const.VIRTUAL_EXHIBITION_PROP_ITEM_NAME_ID,
                "exh_id": exhibition_id,
                "name": name,
            },
        )
        name_row = await _lid(session)
        scp_description = f"scp_{name_row}"

        # 2. Back-fill description on the name row (matches working exhibition format).
        await session.execute(
            text("UPDATE b_iblock_element_property" " SET description = :descr WHERE id = :id"),
            {"descr": scp_description, "id": name_row},
        )

        # 3. Bib property.
        await session.execute(
            text("""
                INSERT INTO b_iblock_element_property
                    (iblock_property_id, iblock_element_id, value, value_type, value_num, description)
                VALUES
                    (:prop_bib, :exh_id, :bib_html, 'text', 0.0, :descr)
            """),
            {
                "prop_bib": const.VIRTUAL_EXHIBITION_PROP_ITEM_BIB_ID,
                "exh_id": exhibition_id,
                "bib_html": bib_html,
                "descr": scp_description,
            },
        )
        bib_row = await _lid(session)

        # 4. Description property.
        await session.execute(
            text("""
                INSERT INTO b_iblock_element_property
                    (iblock_property_id, iblock_element_id, value, value_type, value_num, description)
                VALUES
                    (:prop_desc, :exh_id, :description_html, 'text', 0.0, :descr)
            """),
            {
                "prop_desc": const.VIRTUAL_EXHIBITION_PROP_ITEM_DESC_ID,
                "exh_id": exhibition_id,
                "description_html": description_html,
                "descr": scp_description,
            },
        )
        desc_row = await _lid(session)

        # 5. Image properties — track each row ID for the link value.
        img_rows: list[int] = []
        for file_id in image_file_ids:
            await session.execute(
                text("""
                    INSERT INTO b_iblock_element_property
                        (iblock_property_id, iblock_element_id, value, value_type, value_num, description)
                    VALUES
                        (:prop_img, :exh_id, :file_id, 'text', :file_num, :descr)
                """),
                {
                    "prop_img": const.VIRTUAL_EXHIBITION_PROP_ITEM_IMAGE_ID,
                    "exh_id": exhibition_id,
                    "file_id": str(file_id),
                    "file_num": float(file_id),
                    "descr": scp_description,
                },
            )
            img_rows.append(await _lid(session))

        # 6. Link property — Bitrix uses this to find and group all sub-properties.
        link_value = _build_scp_link_value(
            element_id=exhibition_id,
            prop_name_id=const.VIRTUAL_EXHIBITION_PROP_ITEM_NAME_ID,
            prop_bib_id=const.VIRTUAL_EXHIBITION_PROP_ITEM_BIB_ID,
            prop_desc_id=const.VIRTUAL_EXHIBITION_PROP_ITEM_DESC_ID,
            prop_img_id=const.VIRTUAL_EXHIBITION_PROP_ITEM_IMAGE_ID,
            name_row=name_row,
            bib_row=bib_row,
            desc_row=desc_row,
            img_rows=img_rows,
            img_file_ids=image_file_ids,
            scp_desc=scp_description,
        )
        await session.execute(
            text("""
                INSERT INTO b_iblock_element_property
                    (iblock_property_id, iblock_element_id, value, value_type, value_num, description)
                VALUES
                    (:prop_link, :exh_id, :value, 'text', 0.0, :descr)
            """),
            {
                "prop_link": const.VIRTUAL_EXHIBITION_PROP_ITEM_LINK_ID,
                "exh_id": exhibition_id,
                "value": link_value,
                "descr": scp_description,
            },
        )


def _build_scp_link_value(
    element_id: int,
    prop_name_id: int,
    prop_bib_id: int,
    prop_desc_id: int,
    prop_img_id: int,
    name_row: int,
    bib_row: int,
    desc_row: int,
    img_rows: list[int],
    img_file_ids: list[int],
    scp_desc: str,
) -> str:
    """Return the PHP-serialised link value Bitrix expects for a sub-component item.

    Format: a:1:{i:[element_id];a:4:{i:197;a:1:{i:[name_row];s:X:"scp_X";}...}}
    For image rows the inner value is the file ID string, not the scp anchor.
    """
    scp_str = f's:{len(scp_desc)}:"{scp_desc}";'

    def _row_dict(row_ids: list[int]) -> str:
        entries = "".join(f"i:{r};{scp_str}" for r in row_ids)
        return f"a:{len(row_ids)}:{{{entries}}}"

    def _img_row_dict(row_ids: list[int], file_ids: list[int]) -> str:
        entries = "".join(f'i:{r};s:{len(str(fid))}:"{fid}";' for r, fid in zip(row_ids, file_ids))
        return f"a:{len(row_ids)}:{{{entries}}}"

    props = (
        f"i:{prop_name_id};{_row_dict([name_row])}"
        f"i:{prop_bib_id};{_row_dict([bib_row])}"
        f"i:{prop_desc_id};{_row_dict([desc_row])}"
        f"i:{prop_img_id};{_img_row_dict(img_rows, img_file_ids)}"
    )
    return f"a:1:{{i:{element_id};a:4:{{{props}}}}}"
