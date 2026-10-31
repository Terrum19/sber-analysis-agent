"""Источники БД переживают перезапуск бэкенда.

Таблицы PostgreSQL и ClickHouse подключены временными представлениями поверх
``ATTACH``: в файле DuckDB они не сохраняются. Поэтому источник после успешного
подключения запоминается в SQLite (``remember``), при старте переподключается
в фоне (``restore_saved_sources``), а по ``DELETE /api/datasources/{id}``
отключается и забывается (``forget``). Пароль на диск не попадает: он берётся
из ``POSTGRES_PASSWORD__<identity>`` или ``POSTGRES_PASSWORD``, у ClickHouse —
из ``CLICKHOUSE_PASSWORD__<identity>`` или ``CLICKHOUSE_PASSWORD``.
"""

import asyncio
import logging
import os

import duckdb
from dotenv import dotenv_values

from app.config import ENV_FILES, settings
from app.core.data import ingest
from app.core.data.connectors import clickhouse
from app.core.data.connectors.base import BaseConnector
from app.core.data.connectors.clickhouse import ClickHouseConnector
from app.core.data.connectors.factory import build_connector
from app.core.data.connectors.pg_dsn import parse_dsn, quote_value
from app.core.data.connectors.postgres import PostgresConnector
from app.core.memory.datasource_store import DatasourceRecord, DatasourceStore
from app.core.memory.profile_memory import ProfileMemory

logger = logging.getLogger(__name__)

PASSWORD_VARIABLES = {"postgres": "POSTGRES_PASSWORD", "clickhouse": "CLICKHOUSE_PASSWORD"}
RESTORED_KINDS = tuple(PASSWORD_VARIABLES)


def remember(
    connector: BaseConnector, description: str | None, store: DatasourceStore | None = None
) -> None:
    """Запомнить подключённый источник PostgreSQL или ClickHouse (DSN — без пароля)."""
    if isinstance(connector, PostgresConnector):
        kind, schema = "postgres", connector.schema
    elif isinstance(connector, ClickHouseConnector):
        kind, schema = "clickhouse", None
    else:
        return
    record = DatasourceRecord(
        identity=connector.identity,
        kind=kind,
        dsn=connector.dsn.masked(),
        db_schema=schema,
        description=description,
    )
    (store or DatasourceStore()).save(record)


def password_for(identity: str, kind: str = "postgres") -> str | None:
    """Пароль источника: <VAR>__<identity>, иначе общий <VAR> (VAR — по kind в PASSWORD_VARIABLES).

    Окружение процесса важнее файлов .env, backend/.env важнее корневого — как в Settings.
    """
    variable = PASSWORD_VARIABLES[kind]
    sources = [os.environ, *(dotenv_values(path) for path in reversed(ENV_FILES))]
    for name in (f"{variable}__{identity}", variable):
        for values in sources:
            if values.get(name):
                return values[name]
    return None


def connection_string(record: DatasourceRecord) -> str | None:
    """Строка подключения сохранённого источника с паролем из env (None — пароля нет)."""
    dsn = clickhouse.parse_dsn(record.dsn) if record.kind == "clickhouse" else parse_dsn(record.dsn)
    if dsn.password is None:
        return record.dsn
    password = password_for(record.identity, record.kind)
    if password is None:
        return None
    if record.kind == "clickhouse":
        return dsn.with_password(password)
    return f"{dsn.connection_string()} password={quote_value(password)}"


def restore_source(db, record: DatasourceRecord, dsn: str) -> list[str]:
    connector = build_connector(record.kind, dsn, record.db_schema)
    with db.lock:
        return ingest.restore(connector, db)


async def restore_saved_sources(db, store: DatasourceStore | None = None) -> list[str]:
    """Переподключить сохранённые источники по очереди; вернуть identity восстановленных.

    Каждый источник — в отдельном потоке с таймаутом POSTGRES_RESTORE_TIMEOUT_S
    (общий для PostgreSQL и ClickHouse). Ошибка источника пишется в лог и не
    мешает остальным. Источники не из RESTORED_KINDS пропускаются.
    """
    try:
        records = await asyncio.to_thread((store or DatasourceStore()).list)
    except Exception:
        logger.exception("Не удалось прочитать сохранённые источники данных")
        return []
    records = [record for record in records if record.kind in RESTORED_KINDS]
    restored = []
    for record in records:
        try:
            dsn = connection_string(record)
            if dsn is None:
                variable = PASSWORD_VARIABLES[record.kind]
                logger.warning(
                    "Источник %s (%s) не восстановлен: задайте пароль в %s__%s или %s",
                    record.identity,
                    record.dsn,
                    variable,
                    record.identity,
                    variable,
                )
                continue
            tables = await asyncio.wait_for(
                asyncio.to_thread(restore_source, db, record, dsn),
                timeout=settings.postgres_restore_timeout_s,
            )
        except (RuntimeError, ValueError, TimeoutError, duckdb.Error) as error:
            logger.warning(
                "Источник %s (%s) не восстановлен: %s: %s",
                record.identity,
                record.dsn,
                type(error).__name__,
                error,
            )
            continue
        except Exception:
            logger.exception("Источник %s (%s) не восстановлен", record.identity, record.dsn)
            continue
        logger.info(
            "Источник %s (%s) восстановлен: %s", record.identity, record.dsn, ", ".join(tables)
        )
        restored.append(record.identity)
    return restored


def forget(db, identity: str, store: DatasourceStore | None = None) -> list[str] | None:
    """Отключить источник и удалить его запись и паспорта его таблиц.

    Возвращает имена снятых представлений; None — такого источника нет.
    """
    store = store or DatasourceStore()
    record = store.get(identity)
    if record is None:
        return None
    connector = build_connector(record.kind, record.dsn, record.db_schema)
    released = []
    with db.lock:
        for alias in connector.previous_aliases(db):
            views = connector.views_of(db, alias)
            connector.release(db, alias)
            released.extend(views)
        memory = ProfileMemory(db)
        for view in released:
            memory.delete(view)
    store.delete(identity)
    return released
