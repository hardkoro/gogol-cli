"""Plain exporter."""

import logging

from gogol_cli.exporters.base import AbstractExporter


LOGGER = logging.getLogger(__name__)


class PlainExporter(AbstractExporter):
    """Plain exporter."""

    def export(self, statistics: list[dict[str, int]]) -> None:
        """Pretty print statistics."""
        LOGGER.info("Pretty printing statistics ...")

        message = self.prepare_message(statistics)

        print(message)
