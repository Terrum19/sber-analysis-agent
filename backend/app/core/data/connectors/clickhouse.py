"""Коннектор ClickHouse через DuckDB.

Аналогично PostgreSQL: единый диалект SQL поверх ClickHouse-таблиц.
"""

from app.core.data.connectors.base import BaseConnector


class ClickHouseConnector(BaseConnector):
    """Подключение ClickHouse (прямое чтение или через http/clickhouse_scanner)."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def connect(self, db) -> list[str]:
        """Подключить ClickHouse-таблицы как представления DuckDB.

        TODO(round-2): чтение метаданных таблиц, создание view.
        """
        raise NotImplementedError
