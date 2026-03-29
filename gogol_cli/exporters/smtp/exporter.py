"""SMTP exporter."""

import logging
import smtplib

from gogol_cli.exporters.base_exporter import AbstractExporter
from .schemas import EmailConfig, SMTPConfig


LOGGER = logging.getLogger(__name__)


class SMTPExporter(AbstractExporter):
    """SMTP exporter."""

    def __init__(self, smtp_config: SMTPConfig, email_config: EmailConfig) -> None:
        """SMTP exporter."""
        self._smtp_config = smtp_config
        self._email_config = email_config

    def export(self, statistics: list[dict[str, int]]) -> None:
        """Export."""
        LOGGER.info("Sending statistics via SMTP ...")

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
