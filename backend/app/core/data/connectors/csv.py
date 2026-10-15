"""Коннектор сырых CSV: загрузка БЕЗ предварительной обработки.

Требование: сырые данные в произвольном виде (CSV) грузятся как есть,
вся предобработка выполняется внутри платформы после загрузки.
"""

from app.core.data.connectors.base import BaseConnector


class CSVConnector(BaseConnector):
    """Подключение CSV-файла(-ов) как представлений DuckDB."""

    def __init__(self, paths: dict[str, str]) -> None:
        """paths: {имя_таблицы: путь_к_csv}."""
        self.paths = paths

    def connect(self, db) -> list[str]:
        """Создать view на каждый CSV через read_csv_auto."""
        for name, path in self.paths.items():
            db.attach_csv(name, path)
        return list(self.paths)
