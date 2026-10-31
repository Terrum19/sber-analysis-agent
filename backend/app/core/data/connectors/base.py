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

    def foreign_keys(self, db) -> list:
        """Внешние ключи из схемы источника между подключёнными таблицами (по умолчанию нет).

        Список relations.Relation с именами таблиц, которые вернул connect().
        """
        return []

    def skip(self, db, table: str, error: Exception) -> bool:
        """Таблицу не удалось прочитать при профилировании: пропустить её?

        True — коннектор убрал таблицу из DuckDB, подключение продолжается
        без неё. По умолчанию нет: сбой профиля отменяет подключение.
        """
        return False

    def rollback(self, db) -> None:
        """Убрать из DuckDB всё, что создал connect() (по умолчанию нечего)."""

    def scrub(self, text: str) -> str:
        """Текст ошибки без секретов источника (по умолчанию как есть)."""
        return text

    def warnings(self, table: str) -> list[str]:
        """Что загрузчик исправил в данных таблицы или не смог исправить (по умолчанию ничего)."""
        return []
