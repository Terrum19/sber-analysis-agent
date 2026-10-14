"""Коннектор PostgreSQL через DuckDB (postgres_scanner).

Позволяет писать SQL в едином диалекте DuckDB к таблицам PostgreSQL,
без предварительной выгрузки/обработки датасета.
"""
from app.core.data.connectors.base import BaseConnector


class PostgresConnector(BaseConnector):
    """Подключение PostgreSQL как вложенной БД DuckDB."""

    def __init__(self, dsn: str, schema: str | None = None) -> None:
        self.dsn = dsn
        self.schema = schema

    def connect(self, db) -> list[str]:
        """ATTACH PostgreSQL и создать view на таблицы.

        TODO(round-2): INSTALL/LOAD postgres_scanner, ATTACH по DSN,
        перечисление таблиц схемы, создание view под единый диалект.
        """
        raise NotImplementedError
