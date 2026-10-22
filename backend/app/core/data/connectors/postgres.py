import hashlib
import logging
import secrets
import time
from typing import NoReturn

import duckdb

from app.config import settings
from app.core.data.connectors.base import BaseConnector
from app.core.data.connectors.pg_dsn import parse_dsn

logger = logging.getLogger(__name__)

EXTENSION = "postgres_scanner"
DEFAULT_SCHEMA = "public"

FAILURE_REASONS = (
    (
        ("password authentication failed", "authentication failed", "no password supplied"),
        "PostgreSQL отклонил учётные данные: проверьте пользователя и пароль.",
    ),
    (
        ("could not translate host name", "name or service not known", "nodename nor servname"),
        "Не удалось найти хост PostgreSQL: проверьте адрес сервера.",
    ),
    (
        (
            "connection refused",
            "timeout expired",
            "network is unreachable",
            "no route to host",
            "connection timed out",
            "server closed the connection unexpectedly",
        ),
        "Сервер PostgreSQL недоступен: проверьте сеть, хост и порт.",
    ),
    (
        ("does not exist",),
        "Указанная база данных или пользователь не существует на сервере PostgreSQL.",
    ),
    (
        ("permission denied",),
        "Недостаточно прав для чтения схемы PostgreSQL.",
    ),
)


class PostgresConnectionError(RuntimeError):
    pass


class PostgresExtensionUnavailableError(RuntimeError):
    pass


def quote_identifier(name: str) -> str:
    escaped = name.replace('"', '""')
    return f'"{escaped}"'


def quote_literal(value: str) -> str:
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def describe_failure(text: str) -> str:
    lowered = text.lower()
    for markers, reason in FAILURE_REASONS:
        if any(marker in lowered for marker in markers):
            return reason
    return "Не удалось подключиться к PostgreSQL."


class PostgresConnector(BaseConnector):
    def __init__(self, dsn: str, schema: str | None = None, max_tables: int | None = None) -> None:
        self.dsn = parse_dsn(dsn)
        self.schema = (schema or "").strip() or DEFAULT_SCHEMA
        default_max = (
            settings.postgres_max_tables
            if settings.postgres_max_tables != 50
            else getattr(settings, "db_max_tables", 50)
        )
        self.max_tables = default_max if max_tables is None else max_tables
        if self.max_tables < 1:
            raise ValueError("Лимит таблиц PostgreSQL должен быть положительным числом.")
        digest = hashlib.sha256(f"{self.dsn.masked()}|{self.schema}".encode()).hexdigest()[:10]
        self.identity = f"pg_{digest}"
        self.alias = ""
        self.sources: dict[str, str] = {}

    def __repr__(self) -> str:
        return f"PostgresConnector(dsn={self.dsn.masked()!r}, schema={self.schema!r})"

    @property
    def source(self) -> str:
        return f"{self.dsn.masked()} (схема {self.schema})"

    def connect(self, db) -> list[str]:
        started = time.perf_counter()
        self.alias = f"{self.identity}_{secrets.token_hex(3)}"
        self.sources = {}
        self.load_extension(db)
        self.attach(db)
        tables = self.list_tables(db)
        for previous in self.previous_aliases(db):
            self.release(db, previous)
        if not tables:
            logger.warning("PostgreSQL %s: в схеме нет таблиц, подключать нечего", self.source)
            self.release(db, self.alias)
            return []
        taken = self.existing_names(db)
        if len(tables) > self.max_tables:
            logger.warning(
                "PostgreSQL %s: в схеме %d таблиц, подключены первые %d (POSTGRES_MAX_TABLES)",
                self.source,
                len(tables),
                self.max_tables,
            )
            tables = tables[: self.max_tables]
        views = [self.create_view(db, table, taken) for table in tables]
        logger.info(
            "PostgreSQL %s: подключено %d таблиц за %.2f с",
            self.source,
            len(views),
            time.perf_counter() - started,
        )
        return views

    def load_extension(self, db) -> None:
        try:
            db.execute(f"INSTALL {EXTENSION}")
        except duckdb.Error as error:
            logger.info("INSTALL %s не выполнен, пробуем LOAD: %s", EXTENSION, error)
        try:
            db.execute(f"LOAD {EXTENSION}")
        except duckdb.Error as error:
            logger.error("LOAD %s не выполнен: %s", EXTENSION, error)
            raise PostgresExtensionUnavailableError(
                f"Расширение DuckDB {EXTENSION} недоступно: в замкнутом контуре его нужно "
                "заранее установить в образ (INSTALL без сети невозможен)."
            ) from None

    def previous_aliases(self, db) -> list[str]:
        rows = db.query(
            "SELECT database_name FROM duckdb_databases() "
            "WHERE type = 'postgres' AND starts_with(database_name, $prefix) "
            "AND database_name <> $current",
            {"prefix": f"{self.identity}_", "current": self.alias},
        )
        return [row["database_name"] for row in rows]

    def release(self, db, alias: str) -> None:
        for view in self.views_of(db, alias):
            db.execute(f"DROP VIEW IF EXISTS temp.main.{quote_identifier(view)}")
        db.execute(f"DETACH DATABASE IF EXISTS {alias}")
        db.execute(f"DROP TEMPORARY SECRET IF EXISTS {alias}_secret")

    def views_of(self, db, alias: str) -> list[str]:
        rows = db.query(
            "SELECT view_name FROM duckdb_views() "
            "WHERE temporary AND NOT internal AND contains(sql, $marker)",
            {"marker": f" {alias}."},
        )
        return [row["view_name"] for row in rows]

    def existing_names(self, db) -> set[str]:
        rows = db.query(
            "SELECT view_name AS name FROM duckdb_views() "
            "WHERE schema_name = 'main' AND database_name IN ('temp', current_database()) "
            "UNION ALL SELECT table_name FROM duckdb_tables() "
            "WHERE schema_name = 'main' AND database_name IN ('temp', current_database())"
        )
        return {row["name"].lower() for row in rows}

    def attach(self, db) -> None:
        options = "TYPE postgres, READ_ONLY"
        secret = f"{self.alias}_secret"
        try:
            if self.dsn.password:
                db.execute(
                    f"CREATE OR REPLACE TEMPORARY SECRET {secret} "
                    "(TYPE postgres, PASSWORD $password)",
                    {"password": self.dsn.password},
                )
                options += f", SECRET {secret}"
            db.execute(
                f"ATTACH {quote_literal(self.dsn.connection_string())} AS {self.alias} ({options})"
            )
        except duckdb.Error as error:
            self.fail(db, error)

    def list_tables(self, db) -> list[str]:
        try:
            rows = db.query(
                f"SELECT table_name FROM {self.alias}.information_schema.tables "
                "WHERE table_schema = $schema ORDER BY table_name",
                {"schema": self.schema},
            )
        except duckdb.Error as error:
            self.fail(db, error)
        return [row["table_name"] for row in rows]

    def create_view(self, db, table: str, taken: set[str]) -> str:
        candidates = [
            table,
            f"{self.dsn.params.get('dbname', 'pg')}_{table}",
            f"{self.alias}_{table}",
        ]
        view = next((name for name in candidates if name.lower() not in taken), None)
        suffix = 2
        while view is None:
            numbered = f"{self.alias}_{table}_{suffix}"
            view = numbered if numbered.lower() not in taken else None
            suffix += 1
        if view != table:
            logger.warning(
                "PostgreSQL %s: имя «%s» уже занято, таблица подключена как «%s»",
                self.source,
                table,
                view,
            )
        taken.add(view.lower())
        self.sources[view] = table
        target = f"{self.alias}.{quote_identifier(self.schema)}.{quote_identifier(table)}"
        db.execute(
            f"CREATE OR REPLACE TEMP VIEW {quote_identifier(view)} AS SELECT * FROM {target}"
        )
        return view

    def column_comments(self, db, table: str) -> dict[str, str]:
        """TEMP VIEW не переносит COMMENT ON COLUMN: col_description() читается в PostgreSQL."""
        source = self.sources.get(table)
        if source is None:
            return {}
        sql = (
            "SELECT a.attname::text AS column_name, "
            "col_description(a.attrelid, a.attnum) AS comment "
            "FROM pg_catalog.pg_attribute a "
            "JOIN pg_catalog.pg_class c ON c.oid = a.attrelid "
            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE n.nspname = {quote_literal(self.schema)} AND c.relname = {quote_literal(source)} "
            "AND a.attnum > 0 AND NOT a.attisdropped "
            "AND col_description(a.attrelid, a.attnum) IS NOT NULL"
        )
        try:
            # Только SELECT *: DuckDB 2.0 не применяет к postgres_query проекцию и
            # фильтр внешнего запроса — колонки приходят в порядке запроса PostgreSQL
            # под чужими именами, а фильтр проверяет не ту колонку.
            rows = db.query(
                f"SELECT * FROM postgres_query({quote_literal(self.alias)}, {quote_literal(sql)})"
            )
        except duckdb.Error as error:
            logger.warning(
                "PostgreSQL %s: не удалось прочитать комментарии колонок «%s»: %s",
                self.source,
                table,
                self.dsn.scrub(str(error)),
            )
            return {}
        return {row["column_name"]: row["comment"] for row in rows}

    def fail(self, db, error: Exception) -> NoReturn:
        detail = self.dsn.scrub(str(error))
        logger.warning("PostgreSQL %s: ошибка подключения: %s", self.source, detail)
        try:
            self.release(db, self.alias)
        except duckdb.Error:
            logger.warning("PostgreSQL %s: не удалось освободить %s", self.source, self.alias)
        raise PostgresConnectionError(
            f"{describe_failure(detail)} Источник: {self.source}."
        ) from None
