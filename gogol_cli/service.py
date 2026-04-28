"""Gogol CLI service."""

import io
import logging
import re
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from gogol_cli import constants as const
from gogol_cli.clients import DatabaseClient
from gogol_cli.exceptions import GogolCLIException, SSHNotConfiguredError
from gogol_cli.exhibition.schemas import ParsedExhibition
from gogol_cli.schemas import Event
from gogol_cli.ssh_file_manager import SSHFileManager
from gogol_cli.virtual_exhibition.schemas import ParsedVirtualExhibition

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

    async def create_exhibition(
        self,
        parsed: ParsedExhibition,
        active_from: datetime,
    ) -> None:
        """Create an exhibition and its books from parsed docx data.

        Uploads cover images via SSH, inserts file records, creates a section
        for books, inserts the exhibition element and one book element per book,
        and sets the bibliographic properties on each book.

        Args:
            parsed: The exhibition data produced by ``parse_exhibition_folder``.
            active_from: The ``active_from`` datetime to set on all created elements.
        """
        from PIL import Image

        LOGGER.info("Creating exhibition '%s' ...", parsed.title)

        async with self._db.session() as session:
            if not self._dry_run and self._ssh is None:
                raise SSHNotConfiguredError(
                    "An SSH file manager is required to upload images " "but was not provided."
                )

            ssh = self._ssh

            # --- Illustration ---
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

            # --- Book section (created first so we have section_id for properties) ---
            section_id = await self._db.insert_book_section(session, parsed.title)

            # --- Exhibition element ---
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

            # --- Books ---
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

    async def create_virtual_exhibition(
        self,
        parsed: ParsedVirtualExhibition,
    ) -> None:
        """Create a virtual exhibition and upload its images via SSH.

        Resizes images whose largest dimension exceeds the configured maximum,
        uploads each image to the remote server, inserts file records in the
        database, and stores the exhibition element together with all item
        properties.

        Args:
            parsed: The virtual exhibition data produced by
                ``parse_virtual_exhibition_folder``.
        """
        LOGGER.info("Creating virtual exhibition '%s' …", parsed.title)

        async with self._db.session() as session:
            if not self._dry_run and self._ssh is None:
                raise SSHNotConfiguredError(
                    "An SSH file manager is required to upload images but was not provided."
                )

            ssh = self._ssh

            # ── Preview / detail image ───────────────────────────────────────
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

            # ── Exhibition element ───────────────────────────────────────────
            # The element is active from today until one day after the display end date.
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

            # ── Items ────────────────────────────────────────────────────────
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


def _resize_image(data: bytes, max_dim: int) -> tuple[bytes, int, int]:
    """Resize *data* so the largest dimension does not exceed *max_dim*.

    Returns:
        (resized_bytes, width, height)  – original bytes if no resize needed.
    """
    from PIL import Image

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
