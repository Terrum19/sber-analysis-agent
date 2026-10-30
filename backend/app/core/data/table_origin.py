"""Откуда таблица и есть ли та же таблица у другого пользователя (#249).

CSV-таблица — постоянная таблица с комментарием «csv:<id источника>/<файл>»;
её узнаёт хэш содержимого: тот же файл даёт тот же хэш под любым именем
таблицы. Хэш не зависит от порядка строк и колонок и считается через md5,
а не ``hash()`` DuckDB: тот не обещает одинаковый результат в разных версиях.

Таблица PostgreSQL или ClickHouse — временное представление поверх
подключения ``<identity>_<суффикс>``; по ``identity`` источник находится в
``DatasourceStore``. Живые данные БД хэшем не сравниваются: та же таблица —
это таблица с тем же именем в той же базе (хост, порт, база, схема).
"""

import hashlib
import re
from dataclasses import dataclass

from app.core.data.connectors import clickhouse, pg_dsn
from app.core.data.connectors.csv import CSV_MARKER
from app.core.data.connectors.postgres import DEFAULT_SCHEMA, quote_identifier
from app.core.memory.datasource_store import DatasourceRecord, DatasourceStore

VIEW_SOURCE = re.compile(r"\b((?:pg|ch)_[0-9a-f]{10})_[0-9a-f]{6}\.")
POSTGRES_PORT = "5432"


@dataclass(frozen=True)
class Origin:
    """Источник таблицы: CSV (id источника и файл) или БД (сохранённый источник)."""

    kind: str
    datasource_id: str | None = None
    file: str | None = None
    record: DatasourceRecord | None = None


def origin(db, table: str) -> Origin | None:
    """Источник таблицы по каталогу DuckDB; None — не CSV и не БД (или таблицы нет)."""
    rows = db.query(
        "SELECT comment FROM duckdb_tables() WHERE schema_name = 'main' AND table_name = $table",
        {"table": table},
    )
    comment = (rows[0]["comment"] or "") if rows else ""
    if comment.startswith(CSV_MARKER):
        source, _, file = comment.removeprefix(CSV_MARKER).rpartition("/")
        return Origin("csv", datasource_id=source or None, file=file)
    identity = view_identity(db, table)
    record = DatasourceStore().get(identity) if identity else None
    return Origin(record.kind, record=record) if record is not None else None


def view_identity(db, table: str) -> str | None:
    """identity источника БД, через который подключено представление; None — не оно."""
    rows = db.query(
        "SELECT sql FROM duckdb_views() WHERE temporary AND view_name = $table", {"table": table}
    )
    match = VIEW_SOURCE.search(rows[0]["sql"] or "") if rows else None
    return match.group(1) if match else None


def content_hash(db, table: str) -> str:
    """Хэш содержимого таблицы: колонки и строки, без учёта их порядка."""
    columns = sorted(db.columns(table))
    values = ", ".join(
        f"coalesce(CAST({quote_identifier(column)} AS VARCHAR), chr(30))" for column in columns
    )
    row = db.query(
        f"SELECT count(*) AS rows, sum(md5_number_upper(concat_ws(chr(31), {values}))) AS total"
        f" FROM {quote_identifier(table)}"
    )[0]
    text = f"{columns}|{row['rows']}|{row['total'] or 0}"
    return hashlib.md5(text.encode()).hexdigest()


def row_count(db, table: str) -> int:
    return db.query(f"SELECT count(*) AS rows FROM {quote_identifier(table)}")[0]["rows"]


def find_csv(db, rows: int, digest: str, prefer: str | None = None) -> str | None:
    """CSV-таблица с тем же содержимым под любым именем; None — такой нет.

    Хэш считается только таблицам с тем же числом строк; ``prefer`` — первой.
    """
    names = [
        row["name"]
        for row in db.query(
            "SELECT table_name AS name FROM duckdb_tables()"
            " WHERE schema_name = 'main' AND starts_with(comment, $marker) ORDER BY table_name",
            {"marker": CSV_MARKER},
        )
    ]
    names.sort(key=lambda name: name != prefer)
    return next(
        (
            name
            for name in names
            if row_count(db, name) == rows and content_hash(db, name) == digest
        ),
        None,
    )


def database_key(kind: str, dsn: str, schema: str | None) -> tuple | None:
    """Хост, порт, база и схема — по ним сравниваются базы двух пользователей."""
    try:
        if kind == "postgres":
            parsed = pg_dsn.parse_dsn(dsn)
            port = parsed.params.get("port") or POSTGRES_PORT
            database = parsed.params.get("dbname", "")
            return kind, parsed.host.lower(), port, database, schema or DEFAULT_SCHEMA
        if kind == "clickhouse":
            parsed = clickhouse.parse_dsn(dsn)
            return kind, parsed.host.lower(), str(parsed.port), parsed.database, None
    except ValueError:
        return None
    return None


def same_database(kind: str, dsn: str, schema: str | None) -> list[str]:
    """identity сохранённых источников, указывающих на ту же базу."""
    key = database_key(kind, dsn, schema)
    if key is None:
        return []
    return [
        record.identity
        for record in DatasourceStore().list()
        if database_key(record.kind, record.dsn, record.db_schema) == key
    ]


def connected(db, identity: str) -> bool:
    """Источник сейчас подключён: есть его представления."""
    rows = db.query(
        "SELECT count(*) AS views FROM duckdb_views() WHERE temporary AND contains(sql, $marker)",
        {"marker": f" {identity}_"},
    )
    return rows[0]["views"] > 0
