"""Подключение источника: таблицы в DuckDB, профиль и описание таблиц в паспорт.

Один порядок для API датасорсов, скриптов загрузки и CLI-прогона:
1. attach() — коннектор создаёт таблицы, сразу за ним каждая таблица
   профилируется и профиль сохраняется в паспорт. Синхронно: без профиля
   планировщик не отберёт колонки к первому вопросу.
2. describe() — если у таблицы есть описание (комментарии колонок в
   источнике или документ-описание), SchemaEnricher переносит из него в
   паспорт названия и описания колонок. Описания нет — ничего не делается,
   LLM не вызывается.
"""

from app.config import settings
from app.core.data.connectors.base import BaseConnector
from app.core.data.profiling import TableProfile, profile_table
from app.core.data.schema_enrichment import SchemaEnricher
from app.core.llm.client import LLMClient
from app.core.memory.profile_memory import ProfileMemory
from app.models.enrichment import ColumnEnrichment


def attach(connector: BaseConnector, db) -> dict[str, TableProfile]:
    """Подключить источник и профилировать его таблицы. Вызывать под db.lock.

    Профилирование упало — подключённые таблицы откатываются (если коннектор
    это умеет), чтобы в DuckDB не осталось таблиц без паспорта.
    """
    tables = connector.connect(db)
    memory = ProfileMemory(db)
    profiles: dict[str, TableProfile] = {}
    try:
        for table in tables:
            profile = profile_table(db, table, approx=settings.profile_approx_quantile)
            memory.save_profile(profile)
            profiles[table] = profile
    except BaseException:
        for table in profiles:
            memory.delete(table)
        rollback = getattr(connector, "rollback", None)
        if rollback is not None:
            rollback(db)
        raise
    return profiles


def restore(connector: BaseConnector, db) -> list[str]:
    """Переподключить сохранённый источник после рестарта. Вызывать под db.lock.

    Таблица с паспортом не профилируется заново, если колонки представления
    совпадают с колонками паспорта; иначе (схема в источнике изменилась или
    паспорта нет) профиль пересчитывается.
    """
    tables = connector.connect(db)
    memory = ProfileMemory(db)
    for table in tables:
        profile = memory.load_profile(table)
        if profile is not None and [column.name for column in profile.columns] == db.columns(table):
            continue
        memory.save_profile(profile_table(db, table, approx=settings.profile_approx_quantile))
    return tables


async def describe(
    connector: BaseConnector,
    db,
    tables: list[str],
    document: str | None = None,
    llm: LLMClient | None = None,
) -> dict[str, list[ColumnEnrichment]]:
    """Перенести описание таблиц в паспорт; таблицы без описания пропускаются.

    document — один документ на весь источник (может описывать несколько
    таблиц). Возвращает записи паспорта по описанным таблицам.
    """
    document = (document or "").strip() or None
    enricher = None
    described = {}
    for table in tables:
        with db.lock:
            comments = connector.column_comments(db, table)
        if not comments and document is None:
            continue
        enricher = enricher or SchemaEnricher(db, llm)
        described[table] = await enricher.enrich(table, comments=comments, document=document)
    return described
