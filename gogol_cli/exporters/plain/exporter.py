"""Plain exporter."""

import logging

from gogol_cli.exporters.base_exporter import AbstractExporter


LOGGER = logging.getLogger(__name__)


class PlainExporter(AbstractExporter):
    """Plain exporter."""

    def export(self, statistics: list[dict[str, int]]) -> None:
        """Print the statistics report to stdout.

        Args:
            statistics: A list of dicts with ``what`` and ``cnt`` keys.
        """
        LOGGER.info("Pretty printing statistics ...")

        message = self.prepare_message(statistics)

        print(message)
