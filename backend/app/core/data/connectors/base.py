"""Базовый интерфейс коннектора источника данных.

Единый контракт для CSV/PostgreSQL/ClickHouse: подготовить объекты в
DuckDB и вернуть их список. Вся трансформация — уже внутри платформы.
"""

from abc import ABC, abstractmethod


class BaseConnector(ABC):
    """Абстрактный коннектор источника."""

    @abstractmethod
    def connect(self, db) -> list[str]:
        """Подключить источник к DuckDB, вернуть имена таблиц/представлений.

        db — экземпляр DuckDBManager.
        """
        raise NotImplementedError

    def column_comments(self, db, table: str) -> dict[str, str]:
        """Описания колонок из источника {колонка: комментарий} (по умолчанию нет).

        table — имя таблицы/представления, которое вернул connect().
        """
        return {}
