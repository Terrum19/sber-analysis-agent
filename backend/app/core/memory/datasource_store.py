"""Подключённые источники БД в SQLite: по ним бэкенд переподключает их после рестарта.

Таблица ``datasources`` по адресу ``settings.sqlite_path``, одна строка на
``identity`` источника. Строка подключения хранится только в маскированном
виде (``PostgresDsn.masked()``): пароль при восстановлении берётся из env.
"""

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasources (
  identity TEXT PRIMARY KEY, kind TEXT NOT NULL, dsn TEXT NOT NULL,
  db_schema TEXT, description TEXT, created_at REAL NOT NULL)
"""

UPSERT = """
INSERT INTO datasources (identity, kind, dsn, db_schema, description, created_at)
VALUES (:identity, :kind, :dsn, :db_schema, :description, :created_at)
ON CONFLICT (identity) DO UPDATE SET
  kind = excluded.kind, dsn = excluded.dsn, db_schema = excluded.db_schema,
  description = excluded.description, created_at = excluded.created_at
"""


@dataclass(frozen=True)
class DatasourceRecord:
    identity: str
    kind: str
    dsn: str
    db_schema: str | None = None
    description: str | None = None
    created_at: float = field(default_factory=time.time)


class DatasourceStore:
    """Сохранённые источники: upsert по identity, список, удаление."""

    def __init__(self, path: str | None = None) -> None:
        self._db_path = Path(path or settings.sqlite_path)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self._db_path))
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                connection.execute(SCHEMA)
                yield connection
        finally:
            connection.close()

    def save(self, record: DatasourceRecord) -> None:
        """Сохранить источник; тот же identity перезаписывает прежнюю строку."""
        with self._transaction() as connection:
            connection.execute(UPSERT, asdict(record))

    def get(self, identity: str) -> DatasourceRecord | None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM datasources WHERE identity = ?", (identity,)
            ).fetchone()
        return None if row is None else DatasourceRecord(**dict(row))

    def list(self) -> list[DatasourceRecord]:
        """Источники в порядке подключения."""
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM datasources ORDER BY created_at, identity"
            ).fetchall()
        return [DatasourceRecord(**dict(row)) for row in rows]

    def delete(self, identity: str) -> bool:
        """Удалить источник; False — такого не было."""
        with self._transaction() as connection:
            cursor = connection.execute("DELETE FROM datasources WHERE identity = ?", (identity,))
        return cursor.rowcount > 0
