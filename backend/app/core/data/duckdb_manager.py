"""Менеджер DuckDB — единый SQL-движок платформы.

Все источники (CSV/PostgreSQL/ClickHouse) подключаются унифицированно
через DuckDB, чтобы SQL применялся к любой сущности под единым
диалектом. Результаты шагов — temp-таблицы (state cache, память ур. 2).
"""

import threading
from pathlib import Path

import duckdb

from app.config import settings
from app.core.data.connectors.csv import load_csv


class DuckDBManager:
    """Обёртка над duckdb: подключение источников, temp-таблицы шагов."""

    def __init__(self, path: str | None = None) -> None:
        self._path = path or settings.duckdb_path
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(self._path)
        self._step_counter = 0
        self.lock = threading.RLock()

    def query(self, sql: str, params: dict | None = None) -> list[dict]:
        """Выполнить SELECT и вернуть список словарей."""
        with self.lock:
            cur = self._con.execute(sql, params or {})
            cols = [column[0] for column in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def execute(self, sql: str, params: dict | None = None) -> None:
        """Выполнить DDL/DML без возврата строк."""
        with self.lock:
            self._con.execute(sql, params or {})

    def save_step_result(self, source_table: str, sql: str) -> str:
        """Сохранить результат шага как temp_result_vN и вернуть его имя.

        Нужно, чтобы при смене визуализации или лёгкой модификации
        отчёта не пересчитывать тяжёлые запросы заново.
        """
        self._step_counter += 1
        target = f"temp_result_v{self._step_counter}"
        self.execute(f"CREATE OR REPLACE TABLE {target} AS SELECT * FROM ({sql})")
        return target

    def to_records(self, table: str, limit: int = 100) -> list[dict]:
        """Выгрузить temp-таблицу в список словарей (для LLM/инсайта)."""
        return self.query(f"SELECT * FROM {table} LIMIT {limit}")

    def attach_csv(self, name: str, path: str) -> None:
        """Подключить сырой CSV как представление (без предобработки)."""
        load_csv(self, name, path)

    def close(self) -> None:
        """Закрыть соединение."""
        self._con.close()
