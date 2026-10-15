"""Память уровня 2: кеш результатов и промежуточных данных.

Результаты выполнения шагов хранятся как temp-таблицы в DuckDB, чтобы
при смене визуализации или лёгкой модификации отчёта не пересчитывать
тяжёлые запросы заново. Реализация — в core/data/duckdb_manager.py.
"""

from app.core.data.duckdb_manager import DuckDBManager


class StateCache:
    """Фасад кеша поверх DuckDBManager."""

    def __init__(self, db: DuckDBManager | None = None) -> None:
        self.db = db or DuckDBManager()

    def put(self, source_table: str, sql: str) -> str:
        """Сохранить результат под уникальным идентификатором."""
        return self.db.save_step_result(source_table, sql)

    def get(self, table: str) -> list[dict]:
        """Достать кешированный срез по ссылке."""
        return self.db.to_records(table)
