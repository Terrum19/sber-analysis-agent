"""Менеджер DuckDB — единый SQL-движок платформы.

Все источники (CSV/PostgreSQL/ClickHouse) подключаются унифицированно
через DuckDB, чтобы SQL применялся к любой сущности под единым
диалектом. Результаты шагов — temp-таблицы (state cache, память ур. 2).
"""

import threading
import time
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

from app.config import settings
from app.core.data.connectors.csv import load_csv

# Код результатов, которые ещё не забрал оркестратор: результаты не шагов
# (гистограмма, кеш) никто не забирает, старые вытесняются.
STEP_CODE_LIMIT = 256


class DuckDBManager:
    """Обёртка над duckdb: подключение источников, temp-таблицы шагов."""

    def __init__(self, path: str | None = None) -> None:
        self._path = path or settings.duckdb_path
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(self._path)
        self._step_counter = 0
        self.lock = threading.RLock()
        self._step_code: OrderedDict[str, tuple[str, str]] = OrderedDict()

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

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Выполнить блок в одной транзакции под lock; при любой ошибке — откат."""
        with self.lock:
            self._con.execute("BEGIN TRANSACTION")
            try:
                yield
            except BaseException:
                self._con.execute("ROLLBACK")
                raise
            self._con.execute("COMMIT")

    def save_step_result(
        self,
        source_table: str,
        sql: str,
        *,
        code: str | None = None,
        language: str | None = "sql",
    ) -> str:
        """Сохранить результат шага как temp_result_vN и вернуть его имя.

        Нужно, чтобы при смене визуализации или лёгкой модификации
        отчёта не пересчитывать тяжёлые запросы заново.

        Код результата для показа в шаге (``step_code``): ``sql`` или
        ``code`` на языке ``language``; ``language=None`` — показывать
        нечего (значения посчитаны вне SQL и вставлены литералами).

        Номер продолжает уже лежащие в файле таблицы: менеджер создаётся
        на каждый запрос, и счётчик с нуля перезаписал бы temp_result_v1,
        на который ссылается прошлый график этой или другой сессии.
        Время создания пишется в ``_meta.result_meta`` — по нему
        ``cleanup_results`` удаляет устаревшие результаты.
        """
        with self.lock:
            self._ensure_result_meta()
            self._step_counter = max(self._step_counter, self._last_step_number())
            while True:
                self._step_counter += 1
                target = f"temp_result_v{self._step_counter}"
                try:
                    self._con.execute(f"CREATE TABLE {target} AS SELECT * FROM ({sql})")
                except duckdb.CatalogException as exc:
                    # Имя успел занять параллельный запрос с другим соединением.
                    if "already exists" not in str(exc):
                        raise
                    continue
                self._con.execute(
                    "INSERT OR REPLACE INTO _meta.result_meta VALUES ($name, $created_at)",
                    {"name": target, "created_at": time.time()},
                )
                if language is not None:
                    self._step_code[target] = (language, (code or sql).strip())
                    while len(self._step_code) > STEP_CODE_LIMIT:
                        self._step_code.popitem(last=False)
                return target

    def step_code(self, table: str) -> tuple[str, str] | None:
        """Забрать код, которым построен результат: (язык, код) или None."""
        with self.lock:
            return self._step_code.pop(table, None)

    def _ensure_result_meta(self) -> None:
        """Создать служебную таблицу времени создания результатов в схеме _meta, если её нет."""
        self._con.execute("CREATE SCHEMA IF NOT EXISTS _meta")
        self._con.execute(
            "CREATE TABLE IF NOT EXISTS _meta.result_meta"
            " (name VARCHAR PRIMARY KEY, created_at DOUBLE)"
        )

    def _last_step_number(self) -> int:
        """Наибольший N среди temp_result_vN текущей базы и уже удалённых (0, если их нет).

        Строки ``_meta.result_meta`` удалённых таблиц остаются: номер не
        используется повторно, и старая ссылка на temp_result_vN не
        укажет на чужие данные.
        """
        rows = self.query(
            """SELECT max(TRY_CAST(substr(name, 14) AS BIGINT)) AS n FROM (
                SELECT table_name AS name FROM information_schema.tables
                WHERE table_catalog = current_database()
                  AND table_name LIKE 'temp\\_result\\_v%' ESCAPE '\\'
                UNION ALL SELECT name FROM _meta.result_meta)"""
        )
        return rows[0]["n"] or 0

    def cleanup_results(self, keep: set[str], older_than_s: int) -> list[str]:
        """Удалить temp_result_vN старше older_than_s секунд, кроме keep; вернуть их имена.

        Таблица без строки в ``_meta.result_meta`` (создана до появления
        учёта или параллельным запросом) получает текущее время и
        живёт ещё полный срок.
        """
        with self.lock:
            self._ensure_result_meta()
            now = time.time()
            rows = self.query(
                """SELECT tables.table_name AS name, meta.created_at
                FROM information_schema.tables AS tables
                LEFT JOIN _meta.result_meta AS meta ON meta.name = tables.table_name
                WHERE tables.table_catalog = current_database()
                  AND tables.table_schema = 'main'
                  AND tables.table_name LIKE 'temp\\_result\\_v%' ESCAPE '\\'"""
            )
            dropped = []
            for row in rows:
                name = row["name"]
                if row["created_at"] is None:
                    self._con.execute(
                        "INSERT OR REPLACE INTO _meta.result_meta VALUES ($name, $created_at)",
                        {"name": name, "created_at": now},
                    )
                    continue
                if name in keep or now - row["created_at"] < older_than_s:
                    continue
                self._con.execute(f'DROP TABLE IF EXISTS "{name}"')
                dropped.append(name)
            return dropped

    def columns(self, table: str) -> list[str]:
        """Имена колонок таблицы или представления по порядку ([] — таблицы нет).

        Ищет и во временных представлениях (через них подключён PostgreSQL),
        но не в служебных схемах вроде ``_meta``. Если имя есть и в ``temp``,
        и в основной базе, колонки берутся из ``temp`` — его выберет и
        ``SELECT * FROM <имя>``.
        """
        rows = self.query(
            """WITH found AS (
                SELECT table_catalog, column_name, ordinal_position
                FROM information_schema.columns
                WHERE table_catalog IN ('temp', current_database())
                  AND table_schema = 'main' AND table_name = $table
            )
            SELECT column_name FROM found
            WHERE table_catalog = (
                SELECT table_catalog FROM found ORDER BY table_catalog <> 'temp' LIMIT 1
            )
            ORDER BY ordinal_position""",
            {"table": table},
        )
        return [row["column_name"] for row in rows]

    def user_tables(self) -> list[str]:
        """Таблицы и представления пользователя по алфавиту.

        Без результатов шагов ``temp_result_vN`` и служебных схем вроде ``_meta``;
        временные представления (PostgreSQL) входят.
        """
        rows = self.query(
            """SELECT table_name AS name FROM duckdb_tables()
            WHERE schema_name = 'main' AND database_name IN ('temp', current_database())
            UNION
            SELECT view_name FROM duckdb_views()
            WHERE NOT internal AND schema_name = 'main'
              AND database_name IN ('temp', current_database())
            EXCEPT
            SELECT table_name FROM duckdb_tables()
            WHERE table_name LIKE 'temp\\_result\\_v%' ESCAPE '\\'
            ORDER BY name"""
        )
        return [row["name"] for row in rows]

    def to_records(self, table: str, limit: int = 100, offset: int = 0) -> list[dict]:
        """Выгрузить temp-таблицу в список словарей (для LLM/инсайта)."""
        page = f" OFFSET {offset}" if offset else ""
        return self.query(f"SELECT * FROM {table} LIMIT {limit}{page}")

    def result_shape(self, table: str) -> tuple[list[dict], int]:
        """Колонки результата шага ({"name", "type"}) и число строк; ([], 0) — таблицы нет."""
        columns = self.query(
            "SELECT column_name AS name, data_type AS type FROM information_schema.columns "
            "WHERE table_name = $tbl ORDER BY ordinal_position",
            {"tbl": table},
        )
        if not columns:
            return [], 0
        return columns, self.query(f'SELECT count(*) AS n FROM "{table}"')[0]["n"]

    def attach_csv(self, name: str, path: str) -> None:
        """Подключить сырой CSV как представление (без предобработки)."""
        load_csv(self, name, path)

    def close(self) -> None:
        """Закрыть соединение."""
        self._con.close()
