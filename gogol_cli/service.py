"""Gogol CLI service."""

import asyncio
import functools
import io
import logging
import re
from datetime import date, datetime, timedelta
from typing import Awaitable, Callable, ParamSpec, TypedDict, TypeVar

from PIL import Image
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from gogol_cli import constants as const
from gogol_cli.clients import DatabaseClient
from gogol_cli.exceptions import GogolCLIException, SSHNotConfiguredError
from gogol_cli.books.schemas import ParsedBookEntry
from gogol_cli.exhibition.schemas import ParsedExhibition
from gogol_cli.news.schemas import ParsedNews
from gogol_cli.schemas import Event
from gogol_cli.ssh_file_manager import SSHFileManager
from gogol_cli.virtual_exhibition.schemas import ParsedVirtualExhibition

LOGGER = logging.getLogger(__name__)

# MySQL error codes for dropped/lost connections (safe to retry: nothing was committed).
_RETRYABLE_MYSQL_ERROR_CODES = {2002, 2003, 2006, 2013}
_MAX_DB_RETRIES = 5
_DB_RETRY_DELAY_SECONDS = 3.0

_P = ParamSpec("_P")
_T = TypeVar("_T")


def _is_retryable_db_error(exc: BaseException) -> bool:
    if not isinstance(exc, DBAPIError):
        return False
    error_code = getattr(exc.orig, "args", (None,))[0]
    return error_code in _RETRYABLE_MYSQL_ERROR_CODES


def _with_db_retry(func: Callable[_P, Awaitable[_T]]) -> Callable[_P, Awaitable[_T]]:
    """Retry a whole service call if it fails due to a dropped DB connection.

    Each call opens (and, on success, commits) its own session, so a fresh
    attempt from scratch is safe — nothing from the failed attempt was committed.
    """

    @functools.wraps(func)
    async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _T:
        for attempt in range(1, _MAX_DB_RETRIES + 1):
            try:
                return await func(*args, **kwargs)
            except DBAPIError as exc:
                if attempt == _MAX_DB_RETRIES or not _is_retryable_db_error(exc):
                    raise
                LOGGER.warning(
                    "Database connection lost (attempt %d/%d), retrying in %.0fs ...",
                    attempt,
                    _MAX_DB_RETRIES,
                    _DB_RETRY_DELAY_SECONDS,
                )
                await asyncio.sleep(_DB_RETRY_DELAY_SECONDS)
        raise AssertionError("unreachable")

    return wrapper


class EventData(TypedDict):
    """Data for creating a new event."""

    name: str
    event_date_time: datetime
    description_html: str
    price: str
    purchase_link: str
    registration_link: str
    tags: str
    address: str
    is_active: bool


_RUSSIAN_MONTHS: dict[str, int] = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}


def _parse_date_time_line(ln: str, month_pattern: str, today: date) -> list[tuple[date, str]]:
    """Parse a single date/time line into (date, time_str) pairs.

    Returns an empty list if the line is not a valid date/time line.
    """
    month_match = re.search(month_pattern, ln)
    if month_match is None:
        return []
    month = _RUSSIAN_MONTHS[month_match.group(0)]

    clean = re.sub(r"https?://\S+", "", ln).strip()
    split_match = re.search(r"\s+в\s+", clean)
    if split_match is None:
        return []
    days_part = clean[: split_match.start()]
    time_part = clean[split_match.end() :]

    days = [int(d) for d in re.findall(r"\d+", days_part)]
    if not days:
        return []

    times: list[str] = []
    for tm in re.finditer(r"\d{1,2}(?::(\d{2}))?", time_part):
        hour = int(tm.group(0).split(":")[0])
        minutes = tm.group(1) or "00"
        times.append(f"{hour:02d}-{minutes}")
    if not times:
        return []

    result: list[tuple[date, str]] = []
    for day in days:
        for time_str in times:
            candidate = date(today.year, month, day)
            if candidate < today:
                candidate = date(today.year + 1, month, day)
            result.append((candidate, time_str))
    return result


def parse_xcopy_text(text: str) -> tuple[str, list[tuple[date, str]]]:
    """Parse a natural language copy instruction into a URL and (date, time) pairs.

    The text is split into lines. Lines containing a Russian month name and a
    time spec (``в HH`` or ``в HH:MM``) are treated as date/time lines; all
    other lines (titles, keyword lines, etc.) are silently ignored.

    Each date/time line may produce multiple copies:

    - Multiple days: ``3, 17 и 24 июня в 19:00`` → three copies
    - Multiple times: ``24 мая в 14 и 16`` → two copies at 14:00 and 16:00
    - Multiple lines: each line becomes its own group

    Multi-line input should be passed as a single quoted shell argument
    (e.g. ``$'URL\\n8 мая в 16:00\\n15, 22, 29 мая в 17:00'``).

    Args:
        text: The instruction text (may contain embedded newlines).

    Returns:
        Tuple of ``(url, date_times)`` where ``date_times`` is a list of
        ``(date, time_str)`` pairs and ``time_str`` is in ``HH-MM`` format.
    """
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]

    url: str | None = None
    for ln in lines:
        url_match = re.search(r"https?://\S+", ln)
        if url_match:
            url = url_match.group(0).split("?")[0]
            break
    if url is None:
        raise GogolCLIException(f"No URL found in: {text!r}")

    month_pattern = "|".join(_RUSSIAN_MONTHS)
    today = datetime.now().date()
    date_times: list[tuple[date, str]] = []
    for ln in lines:
        date_times.extend(_parse_date_time_line(ln, month_pattern, today))

    if not date_times:
        raise GogolCLIException(f"No date/time groups found in: {text!r}")

    return url, date_times


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

    async def _copy_picture_cropped(
        self, session: AsyncSession, picture_id: int | None, width: int, height: int
    ) -> int:
        """Copy a picture, cropping it to fill width x height, as a new b_file record.

        Args:
            session: The active database session.
            picture_id: The ID of the source file record to copy.
            width: Target width to crop/scale to.
            height: Target height to crop/scale to.

        Returns:
            The ID of the newly inserted file record.
        """
        if picture_id is None:
            raise ValueError("Cannot copy picture: picture_id is None")

        old_file = await self._db.get_file_by_id(session, picture_id)
        new_subdir = self._db.generate_new_subdir()

        file_size = old_file.file_size
        if not self._dry_run:
            if self._ssh is None:
                raise SSHNotConfiguredError(
                    "An SSH file manager is required to copy pictures but was not provided."
                )
            data = await self._ssh.download_file(old_file)
            cropped_data, width, height = _crop_to_fill(data, width, height)
            file_size = len(cropped_data)
            await self._ssh.upload_file(cropped_data, new_subdir, old_file.file_name)

        return await self._db.insert_new_file_with_subdir(
            session, new_subdir, old_file.file_name, file_size, height=height, width=width
        )

    async def _index_search_content(
        self,
        event_id: int,
        title: str,
        preview_text: str | None,
        detail_text: str | None,
        tags: str | None,
        active_from: str,
        active_to: str,
    ) -> None:
        """Best-effort: index the event so the calendar's search filter picks it up.

        The event element itself is already committed by the time this runs. The
        b_search_content INSERT has been observed to reliably drop the remote MySQL
        connection on this host, so it gets its own session and retry budget and is
        allowed to fail without rolling back or blocking the rest of the operation —
        worst case the event just doesn't show up in the calendar filter until
        re-indexed by hand.
        """
        for attempt in range(1, _MAX_DB_RETRIES + 1):
            try:
                async with self._db.session() as session:
                    await self._db.insert_search_content(
                        session,
                        event_id,
                        title,
                        preview_text,
                        detail_text,
                        tags,
                        active_from,
                        active_to,
                    )
                    await session.commit()
                return
            except DBAPIError as exc:
                if attempt == _MAX_DB_RETRIES or not _is_retryable_db_error(exc):
                    LOGGER.error(
                        "Could not index event %d for the calendar search filter "
                        "(attempt %d/%d); the event was created but may not appear "
                        "in the calendar until reindexed: %s",
                        event_id,
                        attempt,
                        _MAX_DB_RETRIES,
                        exc,
                    )
                    return
                LOGGER.warning(
                    "Search indexing for event %d lost its DB connection "
                    "(attempt %d/%d), retrying in %.0fs ...",
                    event_id,
                    attempt,
                    _MAX_DB_RETRIES,
                    _DB_RETRY_DELAY_SECONDS,
                )
                await asyncio.sleep(_DB_RETRY_DELAY_SECONDS)

    @_with_db_retry
    async def pin_event(self, event: Event) -> None:
        """Create a pin element for the given event, copying its preview picture.

        Args:
            event: The event to pin.
        """
        LOGGER.info("Pinning event %s ...", event.id)

        async with self._db.session() as session:
            preview_picture_id = await self._copy_picture_cropped(
                session,
                event.preview_picture,
                const.PIN_IMAGE_WIDTH,
                const.PIN_IMAGE_HEIGHT,
            )
            pin_id = await self._db.insert_pin(session, event, preview_picture_id)
            await self._db.set_pin_properties(session, event, pin_id)

            if not self._dry_run:
                await session.commit()

        LOGGER.info("Finished pinning event %s", event.id)

    @_with_db_retry
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
        time_display = new_event_time_str.replace("-", ":")
        LOGGER.info(
            "Copying event %s to %s at %s ...",
            event.id,
            new_event_date_str,
            time_display,
        )

        new_event_date = datetime.strptime(new_event_date_str, const.DATE_FORMAT)

        async with self._db.session() as session:
            preview_picture_id = await self._copy_picture(session, event.preview_picture)
            detail_picture_id = await self._copy_picture(session, event.detail_picture)

            new_event_id, active_from, active_to = await self._db.insert_event_copy(
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

        if not self._dry_run:
            await self._index_search_content(
                new_event_id,
                event.name,
                event.preview_text,
                event.detail_text,
                event.tags,
                active_from,
                active_to,
            )

        time_display = new_event_time_str.replace("-", ":")
        LOGGER.info(
            "Finished copying event %s to %s at %s",
            event.id,
            new_event_date_str,
            time_display,
        )

    @_with_db_retry
    async def add_event(
        self,
        event_data: EventData,
        image_data: bytes | None = None,
        image_filename: str | None = None,
    ) -> None:
        """Create a completely new event in the database.

        Args:
            event_data: Event metadata (name, date, description, etc).
            image_data: Optional image binary data.
            image_filename: Optional image file name.
        """
        name = event_data["name"]
        event_date_time = event_data["event_date_time"]
        description_html = event_data["description_html"]
        price = event_data["price"]
        purchase_link = event_data["purchase_link"]
        registration_link = event_data["registration_link"]
        tags = event_data["tags"]
        address = event_data["address"]
        is_active = event_data["is_active"]

        event_date_str = event_date_time.strftime("%Y-%m-%d")
        event_time_str = event_date_time.strftime("%H-%M")

        LOGGER.info(
            "Creating new event '%s' on %s at %s ...",
            name,
            event_date_str,
            event_time_str.replace("-", ":"),
        )

        async with self._db.session() as session:
            # Upload image if provided
            preview_picture_id = None
            detail_picture_id = None
            if image_data and image_filename:
                # Generate subdir
                new_subdir = self._db.generate_new_subdir()

                # Upload to SSH if not dry run
                if not self._dry_run:
                    if self._ssh is None:
                        raise SSHNotConfiguredError(
                            "An SSH file manager is required to upload images but was not provided."
                        )
                    await self._ssh.upload_file(image_data, new_subdir, image_filename)

                # Insert file record with the subdir we used for SSH
                preview_picture_id = await self._db.insert_new_file_with_subdir(
                    session,
                    new_subdir,
                    image_filename,
                    len(image_data),
                )
                detail_picture_id = preview_picture_id

            # Create preview text (first paragraph of description)
            preview_text = description_html
            if "<p>" in description_html:
                # Extract first paragraph
                match = re.search(r"<p>(.*?)</p>", description_html, re.DOTALL)
                if match:
                    preview_text = f"<p>{match.group(1)[:200]}...</p>"

            # Insert the event element
            parsed_tags = [t.strip() for t in tags.split(",") if t.strip()]
            is_online_title = "онлайн" in name.lower()
            mode_tag = "Онлайн" if is_online_title else "Офлайн"

            # Remove conflicting/duplicate mode tags from user input, then enforce mode first.
            parsed_tags = [tag for tag in parsed_tags if tag.lower() not in {"онлайн", "офлайн"}]
            parsed_tags.insert(0, mode_tag)

            is_free = not price or price.strip() in {"", "0"}
            if is_free and "Бесплатно" not in parsed_tags:
                parsed_tags.append("Бесплатно")
            normalized_tags = ", ".join(parsed_tags) if parsed_tags else None

            new_event_id, active_from, active_to = await self._db.insert_new_event(
                session,
                name=name,
                event_date_time=event_date_time,
                preview_picture_id=preview_picture_id,
                detail_picture_id=detail_picture_id,
                preview_text=preview_text,
                detail_text=description_html,
                tags=normalized_tags,
                is_active=is_active,
            )

            (
                description_buy_ticket,
                phone,
                email,
                inferred_address,
                location_id,
                type_of_activity_id,
                purchase_link_value,
                registration_link_value,
            ) = self._infer_new_event_properties(
                name,
                description_html,
                price,
                purchase_link,
                registration_link,
                address_override=address,
            )

            # Set event properties (date, time, price, link)
            await self._db.set_new_event_properties(
                session,
                new_event_id,
                event_date_time,
                event_time_str,
                price,
                {
                    "purchase_link": purchase_link_value,
                    "registration_link": registration_link_value,
                    "description_buy_ticket": description_buy_ticket,
                    "phone": phone,
                    "email": email,
                    "address": inferred_address,
                    "location_id": location_id,
                    "type_of_activity_id": type_of_activity_id,
                },
            )

            # Add to event section
            await self._db.add_element_to_section(
                session, new_event_id, const.EVENT_IBLOCK_SECTION_ID
            )

            if not self._dry_run:
                await session.commit()

        if not self._dry_run:
            await self._index_search_content(
                new_event_id,
                name,
                preview_text,
                description_html,
                normalized_tags,
                active_from,
                active_to,
            )

        LOGGER.info(
            "Finished creating new event '%s' (id=%d) on %s at %s — https://www.domgogolya.ru/recital/%d/",
            name,
            new_event_id,
            event_date_str,
            event_time_str.replace("-", ":"),
            new_event_id,
        )

    @staticmethod
    def _infer_new_event_properties(
        name: str,
        description_html: str,
        price: str,
        purchase_link: str,
        registration_link: str,
        address_override: str | None = None,
    ) -> tuple[str, str, str, str, int, int, str | None, str | None]:
        """Infer property values for newly added events.

        Args:
            address_override: Optional address to use instead of inferring.

        Returns:
            description_buy_ticket, phone, email, address, location_id,
            type_of_activity_id, purchase_link, registration_link.
        """
        combined = f"{name} {description_html}".lower()
        is_free = not price or price.strip() in {"", "0"} or "бесплат" in combined

        # A registration link is the strongest signal, but events are sometimes
        # announced without one yet while the description already says so.
        has_entry_by_registration = "вход по регистрации" in combined
        has_by_registration = "по регистрации" in combined
        is_registration = bool(registration_link.strip()) or has_by_registration

        if is_registration and is_free:
            if has_entry_by_registration:
                description_buy_ticket = "Мероприятие бесплатное. Вход по регистрации"
            else:
                description_buy_ticket = "Мероприятие бесплатное. По регистрации."
        elif is_free:
            description_buy_ticket = "Мероприятие бесплатное. Вход свободный"
        else:
            description_buy_ticket = "Мероприятие платное"

        if address_override:
            address = address_override
            location_id = const.MOSCOW_LOCATION_ID
        elif "лекторий" in combined or "новое крыло" in combined:
            address = const.LECTURE_HALL_ADDRESS
            location_id = const.DEFAULT_LOCATION_ID
        else:
            address = const.DEFAULT_ADDRESS
            location_id = const.DEFAULT_LOCATION_ID

        type_map: list[tuple[int, tuple[str, ...]]] = [
            (70, ("онлайн-лекц", "online lecture")),
            (69, ("онлайн мастер", "онлайн-мастер")),
            (68, ("кинолектор",)),
            (71, ("мастер-класс", "мастер класс")),
            (72, ("интеллектуаль", "квиз", "викторин")),
            (40, ("лекц",)),
            (41, ("экскурс",)),
            (10, ("спектакл",)),
            (9, ("концерт",)),
            (8, ("творческий вечер",)),
            (121, ("литературный клуб",)),
            (63, ("детск", "студи")),
            (62, ("онлайн", "zoom", "трансляц")),
        ]
        type_of_activity_id = 40
        for type_id, keywords in type_map:
            if any(keyword in combined for keyword in keywords):
                type_of_activity_id = type_id
                break

        purchase_link_value = purchase_link.strip() or None
        registration_link_value = registration_link.strip() or None

        return (
            description_buy_ticket,
            const.DEFAULT_PHONE,
            const.DEFAULT_EMAIL,
            address,
            location_id,
            type_of_activity_id,
            purchase_link_value,
            registration_link_value,
        )

    async def export(self, month_number: int, year_suffix: str) -> list[dict[str, int]]:
        """Collect activity statistics for the given month."""
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

    @_with_db_retry
    async def copy_chronograph(self, month_number: int, year_suffix: str) -> None:
        """Create a new chronograph section and copy entries from 5 years ago."""
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

            async def copy_picture_or_keep(session: AsyncSession, picture_id: int) -> int:
                try:
                    return await self._copy_picture(session, picture_id)
                except FileNotFoundError:
                    LOGGER.warning(
                        "Chronograph picture file %d is missing; keeping the stale reference",
                        picture_id,
                    )
                    return picture_id

            active_from, active_to = self._get_start_and_end_dates(month_number, year_suffix)
            await self._db.copy_chronograph_section(
                session, old_id, new_id, active_from, active_to, copy_picture_or_keep
            )

            if not self._dry_run:
                await session.commit()

        LOGGER.info("Finished copying chronograph for %s/%s", month_number, year_suffix)

    @_with_db_retry
    async def create_exhibition(
        self,
        parsed: ParsedExhibition,
        active_from: datetime,
    ) -> None:
        """Create an exhibition and its books from parsed docx data."""
        LOGGER.info("Creating exhibition '%s' ...", parsed.title)

        async with self._db.session() as session:
            if not self._dry_run and self._ssh is None:
                raise SSHNotConfiguredError(
                    "An SSH file manager is required to upload images but was not provided."
                )

            ssh = self._ssh

            illus_subdir = self._db.generate_new_subdir()
            if not self._dry_run:
                assert ssh is not None
                await ssh.upload_file(
                    parsed.illustration_data, illus_subdir, parsed.illustration_filename
                )
            with Image.open(io.BytesIO(parsed.illustration_data)) as img:
                illus_w, illus_h = img.width, img.height
            illus_ct = _content_type(parsed.illustration_filename)
            illus_file_id = await self._db.insert_new_file(
                session,
                illus_subdir,
                parsed.illustration_filename,
                illus_ct,
                illus_w,
                illus_h,
                len(parsed.illustration_data),
            )

            section_id = await self._db.insert_book_section(session, parsed.title)

            exhibition_id = await self._db.insert_exhibition_element(
                session,
                title=parsed.title,
                preview_text=parsed.preview_text,
                detail_text=parsed.detail_text,
                preview_picture_id=illus_file_id,
                detail_picture_id=illus_file_id,
                active_from=active_from,
            )
            await self._db.set_exhibition_properties(
                session, exhibition_id, section_id, active_from
            )

            for book in parsed.books:
                cover_subdir = self._db.generate_new_subdir()
                if not self._dry_run:
                    assert ssh is not None
                    await ssh.upload_file(book.cover_data, cover_subdir, book.cover_filename)
                with Image.open(io.BytesIO(book.cover_data)) as img:
                    cover_w, cover_h = img.width, img.height
                cover_ct = _content_type(book.cover_filename)
                cover_file_id = await self._db.insert_new_file(
                    session,
                    cover_subdir,
                    book.cover_filename,
                    cover_ct,
                    cover_w,
                    cover_h,
                    len(book.cover_data),
                )
                book_id = await self._db.insert_book_element(
                    session,
                    title=book.bib.title,
                    section_id=section_id,
                    preview_text=book.preview_text,
                    detail_text=book.description,
                    preview_picture_id=cover_file_id,
                    detail_picture_id=cover_file_id,
                    active_from=active_from,
                    sort=book.sort,
                )
                await self._db.set_book_properties(
                    session,
                    book_id=book_id,
                    full_bib_text=_php_serialize_bib(book.bib.full_text),
                    author=book.bib.author,
                    city=book.bib.city,
                    publisher=book.bib.publisher,
                    year=book.bib.year,
                )

            if not self._dry_run:
                await session.commit()

        LOGGER.info(
            "Finished creating exhibition '%s' (id=%d, books=%d)",
            parsed.title,
            exhibition_id,
            len(parsed.books),
        )

    @_with_db_retry
    async def add_books(
        self,
        books: list[ParsedBookEntry],
        images: list[tuple[bytes | None, str | None]],
        section_id: int,
        active_from: datetime,
        section_name: str = "",
    ) -> None:
        """Add books to an existing section (e.g. Новые поступления or Гоголиана).

        Args:
            books: Parsed and interactively confirmed book entries.
            images: Parallel list of (cover_bytes, filename) — None means no image.
            section_id: Existing b_iblock_section ID to add books under.
            active_from: Activation timestamp for each book element.
            section_name: Human-readable section name for log messages.
        """
        label = section_name or str(section_id)
        LOGGER.info("Adding %d books to «%s» ...", len(books), label)

        async with self._db.session() as session:
            if not self._dry_run and self._ssh is None:
                raise SSHNotConfiguredError(
                    "An SSH file manager is required to upload images" " but was not provided."
                )

            ssh = self._ssh

            for book, (cover_data, cover_filename) in zip(books, images):
                cover_file_id: int | None = None
                if cover_data and cover_filename:
                    resized, w, h = _resize_image(cover_data, const.BOOK_MAX_IMAGE_DIM)
                    cover_subdir = self._db.generate_new_subdir()
                    if not self._dry_run:
                        assert ssh is not None
                        await ssh.upload_file(resized, cover_subdir, cover_filename)
                    ct = _content_type(cover_filename)
                    cover_file_id = await self._db.insert_new_file(
                        session,
                        cover_subdir,
                        cover_filename,
                        ct,
                        w,
                        h,
                        len(resized),
                    )

                book_id = await self._db.insert_book_element(
                    session,
                    title=book.bib.title,
                    section_id=section_id,
                    preview_text=book.preview_text,
                    detail_text=book.description,
                    preview_picture_id=cover_file_id,
                    detail_picture_id=cover_file_id,
                    active_from=active_from,
                    sort=book.sort,
                )
                await self._db.set_book_properties(
                    session,
                    book_id=book_id,
                    full_bib_text=_php_serialize_bib(book.bib.full_text),
                    author=book.bib.author,
                    city=book.bib.city,
                    publisher=book.bib.publisher,
                    year=book.bib.year,
                )
                LOGGER.info("  Added: %s", book.bib.title)

            if not self._dry_run:
                await session.commit()

        LOGGER.info("Finished adding %d books to «%s»", len(books), label)

    @_with_db_retry
    async def add_news(
        self,
        parsed: ParsedNews,
        preview_image: tuple[bytes, str],
        gallery_images: list[tuple[bytes, str]],
        tags: str,
        active_from: datetime,
    ) -> int:
        """Add a news element with a preview picture and an optional image gallery.

        Args:
            parsed: The parsed (and interactively confirmed) news title/text.
            preview_image: (bytes, filename) for the element's preview/detail picture.
            gallery_images: Additional (bytes, filename) pairs for the "Галерея" property.
            tags: Tag string (e.g. "Дом Гоголя" or "Партнеры").
            active_from: Activation timestamp for the news element.

        Returns:
            The ID of the newly created news element.
        """
        LOGGER.info("Adding news '%s' ...", parsed.title)

        if not self._dry_run and self._ssh is None:
            raise SSHNotConfiguredError(
                "An SSH file manager is required to upload images but was not provided."
            )
        ssh = self._ssh

        # Resize and upload every image *before* opening a DB session. SFTP transfer
        # of several full-size camera photos can take a while; doing it inside an open
        # transaction leaves the connection idle for long enough that shared-hosting
        # MySQL servers kill it ("Lost connection ... during query"), and every retry
        # then replays the same slow uploads and hits the same wall again.
        preview_data, preview_filename = preview_image
        resized_preview, preview_w, preview_h = _resize_image(
            preview_data, const.NEWS_MAX_IMAGE_DIM
        )
        preview_subdir = self._db.generate_new_subdir()
        if not self._dry_run:
            assert ssh is not None
            await ssh.upload_file(resized_preview, preview_subdir, preview_filename)

        uploaded_gallery: list[tuple[str, str, str, int, int, int]] = []
        for img_data, img_filename in gallery_images:
            img_resized, img_w, img_h = _resize_image(img_data, const.NEWS_MAX_IMAGE_DIM)
            img_subdir = self._db.generate_new_subdir()
            if not self._dry_run:
                assert ssh is not None
                await ssh.upload_file(img_resized, img_subdir, img_filename)
            uploaded_gallery.append(
                (
                    img_subdir,
                    img_filename,
                    _content_type(img_filename),
                    img_w,
                    img_h,
                    len(img_resized),
                )
            )

        # Everything below is now a short burst of fast metadata-only DB statements.
        async with self._db.session() as session:
            preview_file_id = await self._db.insert_new_file(
                session,
                preview_subdir,
                preview_filename,
                _content_type(preview_filename),
                preview_w,
                preview_h,
                len(resized_preview),
            )

            code = await self._db.generate_unique_news_code(session, parsed.title)
            news_id = await self._db.insert_news_element(
                session,
                title=parsed.title,
                code=code,
                preview_text=parsed.preview_text,
                detail_text=parsed.detail_text,
                preview_picture_id=preview_file_id,
                tags=tags,
                active_from=active_from,
                sort=const.NEWS_DEFAULT_SORT,
            )

            # Batched into a couple of multi-row INSERTs rather than one round trip per
            # image: this host has been dropping connections that fire too many separate
            # queries in quick succession.
            gallery_file_ids = await self._db.insert_new_files_batch(session, uploaded_gallery)
            if gallery_file_ids:
                await self._db.insert_news_gallery_images(session, news_id, gallery_file_ids)

            if not self._dry_run:
                await session.commit()

        LOGGER.info("Finished adding news '%s' (id=%d)", parsed.title, news_id)
        return news_id

    @_with_db_retry
    async def create_virtual_exhibition(
        self,
        parsed: ParsedVirtualExhibition,
    ) -> None:
        """Create a virtual exhibition and upload its images via SSH."""
        LOGGER.info("Creating virtual exhibition '%s' ...", parsed.title)

        async with self._db.session() as session:
            if not self._dry_run and self._ssh is None:
                raise SSHNotConfiguredError(
                    "An SSH file manager is required to upload images but was not provided."
                )

            ssh = self._ssh

            preview_data, preview_w, preview_h = _resize_image(
                parsed.preview_image_data, const.VIRTUAL_EXHIBITION_MAX_IMAGE_DIM
            )
            preview_subdir = self._db.generate_new_subdir()
            if not self._dry_run:
                assert ssh is not None
                await ssh.upload_file(preview_data, preview_subdir, parsed.preview_image_filename)
            preview_ct = _content_type(parsed.preview_image_filename)
            preview_file_id = await self._db.insert_new_file(
                session,
                preview_subdir,
                parsed.preview_image_filename,
                preview_ct,
                preview_w,
                preview_h,
                len(preview_data),
            )

            element_active_from = datetime.today()
            element_active_to = parsed.active_to + timedelta(days=1)
            exhibition_id = await self._db.insert_virtual_exhibition_element(
                session,
                title=parsed.title,
                preview_text=parsed.preview_text,
                detail_text=parsed.detail_text,
                preview_picture_id=preview_file_id,
                detail_picture_id=preview_file_id,
                active_from=element_active_from,
                active_to=element_active_to,
            )
            await self._db.set_virtual_exhibition_properties(
                session,
                element_id=exhibition_id,
                subtitle=parsed.subtitle,
                active_from=parsed.active_from,
                active_to=parsed.active_to,
            )

            for item in parsed.items:
                image_file_ids: list[int] = []
                for img_data, img_filename in item.images:
                    resized_data, img_w, img_h = _resize_image(
                        img_data, const.VIRTUAL_EXHIBITION_MAX_IMAGE_DIM
                    )
                    img_subdir = self._db.generate_new_subdir()
                    if not self._dry_run:
                        assert ssh is not None
                        await ssh.upload_file(resized_data, img_subdir, img_filename)
                    img_ct = _content_type(img_filename)
                    file_id = await self._db.insert_new_file(
                        session,
                        img_subdir,
                        img_filename,
                        img_ct,
                        img_w,
                        img_h,
                        len(resized_data),
                    )
                    image_file_ids.append(file_id)

                await self._db.insert_virtual_exhibition_item(
                    session,
                    exhibition_id=exhibition_id,
                    name=item.name,
                    bib_html=_php_serialize_html(item.bib_text),
                    description_html=_php_serialize_html(item.description),
                    image_file_ids=image_file_ids,
                )

            if not self._dry_run:
                await session.commit()

        LOGGER.info(
            "Finished creating virtual exhibition '%s' (id=%d, items=%d)",
            parsed.title,
            exhibition_id,
            len(parsed.items),
        )


def _content_type(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower()
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
    }.get(ext, "application/octet-stream")


def _php_serialize_bib(text: str) -> str:
    """Serialise a bib string to the PHP ``a:2:{...}`` format stored in prop 30."""
    byte_len = len(text.encode("utf-8"))
    return f'a:2:{{s:4:"TEXT";s:{byte_len}:"{text}";s:4:"TYPE";s:4:"HTML";}}'


def _php_serialize_html(text: str) -> str:
    """Serialise an HTML string to the PHP ``a:2:{...}`` format stored in item props."""
    byte_len = len(text.encode("utf-8"))
    return f'a:2:{{s:4:"TEXT";s:{byte_len}:"{text}";s:4:"TYPE";s:4:"HTML";}}'


def _crop_to_fill(data: bytes, target_w: int, target_h: int) -> tuple[bytes, int, int]:
    """Scale and center-crop *data* to exactly fill target_w x target_h.

    Mirrors the site's own resize_cache crop mode so pinned pictures render
    the same way as the ones the events template resizes on its own.

    Returns:
        (cropped_bytes, target_w, target_h)
    """
    with Image.open(io.BytesIO(data)) as img:
        orig_format = img.format or "JPEG"
        w, h = img.width, img.height
        scale = max(target_w / w, target_h / h)
        scaled_w, scaled_h = max(1, round(w * scale)), max(1, round(h * scale))
        scaled = img.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)

        left = (scaled_w - target_w) // 2
        top = (scaled_h - target_h) // 2
        cropped = scaled.crop((left, top, left + target_w, top + target_h))

        buf = io.BytesIO()
        cropped.save(buf, format=orig_format, quality=90)
        return buf.getvalue(), target_w, target_h


def _resize_image(data: bytes, max_dim: int) -> tuple[bytes, int, int]:
    """Resize *data* so the largest dimension does not exceed *max_dim*.

    Returns:
        (resized_bytes, width, height)  – original bytes if no resize needed.
    """
    with Image.open(io.BytesIO(data)) as img:
        orig_format = img.format or "JPEG"
        w, h = img.width, img.height
        if max(w, h) <= max_dim:
            return data, w, h
        ratio = max_dim / max(w, h)
        new_w = max(1, int(w * ratio))
        new_h = max(1, int(h * ratio))
        resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        resized.save(buf, format=orig_format, quality=90)
        return buf.getvalue(), new_w, new_h
