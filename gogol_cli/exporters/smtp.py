"""SMTP exporter."""

import logging
import smtplib

from dataclasses import dataclass

from gogol_cli.exporters.base import AbstractExporter


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SMTPConfig:
    """SMTP config."""

    host: str | None
    port: int | None
    username: str | None
    password: str | None

    @property
    def is_valid(self) -> bool:
        """Validate."""
        return all(
            [
                self.host is not None,
                self.port is not None,
                self.username is not None,
                self.password is not None,
            ]
        )


@dataclass(frozen=True)
class EmailConfig:
    """Email config."""

    from_addr: str | None
    to_addr: str | None
    subject: str | None

    @property
    def is_valid(self) -> bool:
        """Validate."""
        return all(
            [
                self.from_addr is not None,
                self.to_addr is not None,
                self.subject is not None,
            ]
        )


class SMTPExporter(AbstractExporter):
    """SMTP exporter."""

    def __init__(self, smtp_config: SMTPConfig, email_config: EmailConfig) -> None:
        """SMTP exporter."""
        self._smtp_config = smtp_config
        self._email_config = email_config

    def export(self, statistics: list[dict[str, int]]) -> None:
        """Export."""
        LOGGER.info("Sending statistics via SMTP ...")

        assert self._smtp_config.host
        assert self._smtp_config.port
        assert self._smtp_config.username
        assert self._smtp_config.password
        assert self._email_config.from_addr
        assert self._email_config.to_addr

        with smtplib.SMTP(self._smtp_config.host, self._smtp_config.port) as server:
            server.starttls()
            server.login(self._smtp_config.username, self._smtp_config.password)

            header = (
                f"From: {self._email_config.from_addr}\n"
                f"To: {self._email_config.to_addr}\n"
                f"Subject: {self._email_config.subject}\n\n"
            )
            message = self.prepare_message(statistics)

            server.sendmail(
                from_addr=self._email_config.from_addr,
                to_addrs=[self._email_config.to_addr],
                msg=f"{header}{message}".encode("utf-8"),
            )

        LOGGER.info("Finished sending statistics via SMTP")
