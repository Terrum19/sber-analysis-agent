"""Коннектор ClickHouse через нативное расширение DuckDB clickhouse_scanner."""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import NoReturn

import duckdb

from app.config import settings
from app.core.data.connectors.base import BaseConnector

logger = logging.getLogger(__name__)

EXTENSION = "clickhouse_scanner"
URI_SCHEMES = ("clickhouse", "clickhouses")
DEFAULT_NATIVE_PORT = 9000
DEFAULT_NATIVE_SECURE_PORT = 9440
DEFAULT_DATABASE = "default"
DEFAULT_USER = "default"
MASK = "***"

NO_DATABASE = "Указанная база данных не существует на сервере ClickHouse."
NO_TABLE = "Указанная таблица не существует на сервере ClickHouse."
BAD_CREDENTIALS = "ClickHouse отклонил учётные данные: проверьте пользователя и пароль."
NO_ACCESS = "Недостаточно прав для чтения данных ClickHouse."
UNREACHABLE = "Сервер ClickHouse недоступен: проверьте сеть, хост и порт."

# Коды из src/Common/ErrorCodes.cpp; в тексте ошибки они идут как «Code: 81» или
# «ClickHouse error 81».
CLICKHOUSE_ERROR_CODES: dict[int, str] = {
    60: NO_TABLE,  # UNKNOWN_TABLE
    81: NO_DATABASE,  # UNKNOWN_DATABASE
    102: UNREACHABLE,  # UNEXPECTED_PACKET_FROM_SERVER: обычно не тот порт
    159: UNREACHABLE,  # TIMEOUT_EXCEEDED
    192: BAD_CREDENTIALS,  # UNKNOWN_USER
    193: BAD_CREDENTIALS,  # WRONG_PASSWORD
    194: BAD_CREDENTIALS,  # REQUIRED_PASSWORD
    209: UNREACHABLE,  # SOCKET_TIMEOUT
    210: UNREACHABLE,  # NETWORK_ERROR
    497: NO_ACCESS,  # ACCESS_DENIED
    516: BAD_CREDENTIALS,  # AUTHENTICATION_FAILED
}
ERROR_CODE = re.compile(r"\b(?:clickhouse error|code:?)\s*(\d+)\b", re.IGNORECASE)

FAILURE_REASONS = (
    (
        ("unknown_database", "unknown database"),
        NO_DATABASE,
    ),
    (
        ("unknown_table", "unknown table"),
        NO_TABLE,
    ),
    (
        (
            "password authentication failed",
            "authentication failed",
            "wrong password",
            "credentials",
        ),
        BAD_CREDENTIALS,
    ),
    (
        (
            "could not translate host name",
            "name or service not known",
            "nodename nor servname",
            "getaddrinfo failed",
        ),
        "Не удалось найти хост ClickHouse: проверьте адрес сервера.",
    ),
    (
        ("failed to connect to clickhouse",),
        UNREACHABLE,
    ),
    (
        (
            "connection refused",
            "timeout expired",
            "timed out",
            "network is unreachable",
            "no route to host",
            "connection timed out",
            "server closed the connection",
            "remote end closed",
            "operation not permitted",
        ),
        UNREACHABLE,
    ),
    (
        ("access denied", "not allowed", "permission denied"),
        NO_ACCESS,
    ),
)


class ClickHouseConnectionError(RuntimeError):
    pass


class ClickHouseExtensionUnavailableError(RuntimeError):
    pass


def quote_identifier(name: str) -> str:
    escaped = name.replace('"', '""')
    return f'"{escaped}"'


def quote_literal(value: str) -> str:
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def describe_failure(text: str) -> str:
    """Причина сбоя по тексту ошибки DuckDB: у её исключений нет кода ClickHouse."""
    for match in ERROR_CODE.finditer(text):
        reason = CLICKHOUSE_ERROR_CODES.get(int(match.group(1)))
        if reason is not None:
            return reason
    lowered = text.lower()
    # «Database `x` doesn't exist» и «Table x.y doesn't exist» без кода.
    missing = re.search(r"\b(database|table) \S+ (?:does not|doesn't) exist", lowered)
    if missing:
        return NO_DATABASE if missing.group(1) == "database" else NO_TABLE
    for markers, reason in FAILURE_REASONS:
        if any(marker in lowered for marker in markers):
            return reason
    return "Не удалось подключиться к ClickHouse."


@dataclass(frozen=True)
class ClickHouseDsn:
    host: str
    port: int
    database: str = DEFAULT_DATABASE
    user: str = DEFAULT_USER
    password: str | None = field(default=None, repr=False)
    secure: bool = False

    def masked(self) -> str:
        scheme = "clickhouses" if self.secure else "clickhouse"
        secret = f":{MASK}" if self.password else ""
        auth = f"{self.user}{secret}@" if self.user or secret else ""
        return f"{scheme}://{auth}{self.host}:{self.port}/{self.database}"

    def connection_string(self) -> str:
        """Строка параметров подключения без пароля для ATTACH."""
        secure = " secure=true" if self.secure else ""
        return f"host={self.host} port={self.port} user={self.user} dbname={self.database}{secure}"

    def scrub(self, text: str) -> str:
        if not self.password:
            return text
        return text.replace(self.password, MASK)

    def __repr__(self) -> str:
        return f"ClickHouseDsn({self.masked()!r})"

    def __str__(self) -> str:
        return self.masked()


def parse_dsn(raw: str, default_database: str | None = None) -> ClickHouseDsn:
    text = (raw or "").strip()
    if not text:
        raise ValueError("Строка подключения ClickHouse пуста.")
    if "://" not in text:
        raise ValueError(
            "Не удалось разобрать строку подключения ClickHouse: "
            "ожидается clickhouse://user:password@host:port/dbname"
        )
    parts = urllib.parse.urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme not in URI_SCHEMES:
        raise ValueError(
            f"Схема «{parts.scheme}» не поддерживается: ожидается clickhouse:// или clickhouses://."
        )
    if not parts.hostname:
        raise ValueError("В строке подключения ClickHouse не указан хост.")
    try:
        port = parts.port
    except ValueError:
        raise ValueError("В строке подключения ClickHouse указан некорректный порт.") from None

    secure = scheme == "clickhouses"
    if port is None:
        port = DEFAULT_NATIVE_SECURE_PORT if secure else DEFAULT_NATIVE_PORT
    if port == 8123:
        raise ValueError(
            "Порт 8123 — HTTP-интерфейс ClickHouse. Для этого коннектора "
            "используйте native-порт 9000 или защищённый 9440."
        )

    user = urllib.parse.unquote(parts.username) if parts.username else DEFAULT_USER
    password = urllib.parse.unquote(parts.password) if parts.password is not None else None

    path_db = urllib.parse.unquote(parts.path.lstrip("/"))
    db = (default_database or "").strip() or path_db or DEFAULT_DATABASE

    return ClickHouseDsn(
        host=urllib.parse.unquote(parts.hostname),
        port=port,
        database=db,
        user=user,
        password=password,
        secure=secure,
    )


class ClickHouseConnector(BaseConnector):
    """Коннектор ClickHouse: нативное расширение DuckDB clickhouse_scanner с TEMP VIEW."""

    def __init__(
        self,
        dsn: str,
        database: str | None = None,
        max_tables: int | None = None,
    ) -> None:
        self.dsn = parse_dsn(dsn, default_database=database)
        self.max_tables = settings.db_max_tables if max_tables is None else max_tables
        if self.max_tables < 1:
            raise ValueError("Лимит таблиц ClickHouse должен быть положительным числом.")

        digest = hashlib.sha256(f"{self.dsn.masked()}".encode()).hexdigest()[:10]
        self.identity = f"ch_{digest}"
        self.alias = ""
        self.sources: dict[str, str] = {}
        # Предупреждения по всему источнику; не атрибут warnings — это метод коннектора.
        self.notes: list[str] = []

    def __repr__(self) -> str:
        return f"ClickHouseConnector(dsn={self.dsn.masked()!r})"

    @property
    def source(self) -> str:
        return self.dsn.masked()

    def connect(self, db) -> list[str]:
        started = time.perf_counter()
        self.alias = f"{self.identity}_{secrets.token_hex(3)}"
        self.sources = {}
        self.notes = []

        self.load_extension(db)
        self.attach(db)
        tables = self.list_tables(db)

        for previous in self.previous_aliases(db):
            self.release(db, previous)

        if not tables:
            logger.warning(
                "ClickHouse %s: в базе данных нет таблиц, подключать нечего", self.source
            )
            self.release(db, self.alias)
            return []

        taken = self.existing_names(db)
        if len(tables) > self.max_tables:
            logger.warning(
                "ClickHouse %s: в базе данных %d таблиц, подключены первые %d (DB_MAX_TABLES)",
                self.source,
                len(tables),
                self.max_tables,
            )
            tables = tables[: self.max_tables]
            self.notes.append(
                f"Подключены первые {self.max_tables} таблиц из-за ограничения DB_MAX_TABLES."
            )

        try:
            views = [self.create_view(db, table, taken) for table in tables]
        except duckdb.Error as error:
            self.fail(db, error)
        logger.info(
            "ClickHouse %s: подключено %d таблиц за %.2f с",
            self.source,
            len(views),
            time.perf_counter() - started,
        )
        return views

    def load_extension(self, db) -> None:
        """Загрузка DuckDB community extension clickhouse_scanner."""
        try:
            db.execute(f"INSTALL {EXTENSION} FROM community")
        except duckdb.Error as error:
            logger.info("INSTALL %s FROM community не выполнен, пробуем LOAD: %s", EXTENSION, error)

        try:
            db.execute(f"LOAD {EXTENSION}")
        except duckdb.Error as error:
            logger.error("LOAD %s не выполнен: %s", EXTENSION, error)
            raise ClickHouseExtensionUnavailableError(
                f"Расширение DuckDB {EXTENSION} недоступно: в замкнутом контуре его "
                "нужно заранее установить в образ (INSTALL без сети невозможен)."
            ) from None

    def attach(self, db) -> None:
        """Монтирование базы ClickHouse через community extension и DuckDB Secrets."""
        options = "TYPE clickhouse, READ_ONLY"
        secret = f"{self.alias}_secret"
        try:
            if self.dsn.password:
                db.execute(
                    f"CREATE OR REPLACE TEMPORARY SECRET {secret} "
                    "(TYPE clickhouse, PASSWORD $password)",
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
                f"SELECT table_name AS name FROM duckdb_tables() "
                f"WHERE database_name = '{self.alias}' AND schema_name = $schema "
                "ORDER BY table_name",
                {"schema": self.dsn.database},
            )
            return [row["name"] for row in rows]
        except duckdb.Error as error:
            self.fail(db, error)

    def create_view(self, db, table: str, taken: set[str]) -> str:
        candidates = [
            table,
            f"{self.dsn.database}_{table}",
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
                "ClickHouse %s: имя «%s» уже занято, таблица подключена как «%s»",
                self.source,
                table,
                view,
            )
        taken.add(view.lower())
        self.sources[view] = table

        target = f"{self.alias}.{quote_identifier(self.dsn.database)}.{quote_identifier(table)}"
        db.execute(
            f"CREATE OR REPLACE TEMP VIEW {quote_identifier(view)} AS SELECT * FROM {target}"
        )
        return view

    def views_of(self, db, alias: str) -> list[str]:
        rows = db.query(
            "SELECT view_name FROM duckdb_views() "
            "WHERE temporary AND NOT internal AND contains(sql, $marker)",
            {"marker": f" {alias}."},
        )
        return [row["view_name"] for row in rows]

    def previous_aliases(self, db) -> list[str]:
        rows = db.query(
            "SELECT database_name FROM duckdb_databases() "
            "WHERE type = 'clickhouse' AND starts_with(database_name, $prefix) "
            "AND database_name <> $current",
            {"prefix": f"{self.identity}_", "current": self.alias},
        )
        return [row["database_name"] for row in rows]

    def release(self, db, alias: str) -> None:
        for view in self.views_of(db, alias):
            db.execute(f"DROP VIEW IF EXISTS temp.main.{quote_identifier(view)}")
        db.execute(f"DETACH DATABASE IF EXISTS {alias}")
        db.execute(f"DROP TEMPORARY SECRET IF EXISTS {alias}_secret")

    def rollback(self, db) -> None:
        """Освободить подключение и временные представления после сбоя ingest."""
        if self.alias:
            self.release(db, self.alias)

    def scrub(self, text: str) -> str:
        return self.dsn.scrub(text)

    def warnings(self, table: str) -> list[str]:
        """Предупреждения источника: относятся к каждой его таблице."""
        return list(self.notes)

    def existing_names(self, db) -> set[str]:
        rows = db.query(
            "SELECT view_name AS name FROM duckdb_views() "
            "WHERE schema_name = 'main' AND database_name IN ('temp', current_database()) "
            "UNION ALL SELECT table_name FROM duckdb_tables() "
            "WHERE schema_name = 'main' AND database_name IN ('temp', current_database())"
        )
        return {row["name"].lower() for row in rows}

    def column_comments(self, db, table: str) -> dict[str, str]:
        source = self.sources.get(table)
        if source is None:
            return {}

        sql = (
            "SELECT name AS column_name, comment FROM system.columns "
            f"WHERE database = {quote_literal(self.dsn.database)} "
            f"AND table = {quote_literal(source)} "
            "AND comment != '' "
            "ORDER BY position"
        )
        try:
            rows = db.query(
                f"SELECT column_name, comment FROM "
                f"clickhouse_query({quote_literal(self.alias)}, {quote_literal(sql)}) "
                "WHERE comment IS NOT NULL AND comment != ''"
            )
            return {row["column_name"]: row["comment"] for row in rows}
        except duckdb.Error as error:
            logger.warning(
                "ClickHouse %s: не удалось прочитать комментарии колонок «%s»: %s",
                self.source,
                table,
                self.dsn.scrub(str(error)),
            )
            return {}

    def fail(self, db, error: Exception) -> NoReturn:
        detail = self.dsn.scrub(str(error))
        logger.warning("ClickHouse %s: ошибка подключения: %s", self.source, detail)
        try:
            self.release(db, self.alias)
        except duckdb.Error:
            logger.warning("ClickHouse %s: не удалось освободить %s", self.source, self.alias)
        reason = describe_failure(detail)
        raise ClickHouseConnectionError(f"{reason} Источник: {self.source}.") from None
