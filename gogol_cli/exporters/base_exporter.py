"""Base exporter."""

from abc import ABC, abstractmethod

import textwrap


class AbstractExporter(ABC):
    """Abstract exporter."""

    MESSAGE_TEMPLATE = textwrap.dedent(
        """
        Добрый день!
        
        Уникальные действия:
          – страниц добавлено на сайт: {pages_added}
          – файлов добавлено на сайт: {files_added}
          – страниц обновлено на сайте: {pages_updated}
          – поисковых индексов обновлено на сайте: {search_index_updated}
        
        Постоянные действия:
          – оперативное размещение материалов, обработка текстов и изображений для сайта.
          – составление ежемесячного Хронографа.
          – поддержка актуализации раздела «Информация» нормативными документами.
          – оптимизация времени исполнения страниц, HTML- и CSS-кода и метаописания.
          – проведение мероприятий по резервному копированию сайта.
          – сбор и ведение статистики.
        
        С уважением,
        Евгений
        """
    )

    def prepare_message(self, statistics: list[dict[str, int]]) -> str:
        """Prepare message."""
        pages_added = 0
        files_added = 0
        pages_updated = 0
        search_index_updated = 0

        for stat in statistics:
            if stat["what"] == "01 added":
                pages_added = stat["cnt"]
            elif stat["what"] == "02 files":
                files_added = stat["cnt"]
            elif stat["what"] == "03 updated":
                pages_updated = stat["cnt"]
            elif stat["what"] == "04 search changes":
                search_index_updated = stat["cnt"]

        message = self.MESSAGE_TEMPLATE.format(
            pages_added=pages_added,
            files_added=files_added,
            pages_updated=pages_updated,
            search_index_updated=search_index_updated,
        )

        return message

    @abstractmethod
    def export(self, statistics: list[dict[str, int]]) -> None:
        """Export."""
