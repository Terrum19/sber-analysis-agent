"""Подключение источника: таблицы в DuckDB, профиль и описание таблиц в паспорт.

Один порядок для API датасорсов, скриптов загрузки и CLI-прогона:
1. attach() — коннектор создаёт таблицы, сразу за ним каждая таблица
   профилируется и профиль сохраняется в паспорт. Синхронно: без профиля
   планировщик не отберёт колонки к первому вопросу. Таблицу, которую
   нельзя прочитать (нет прав), коннектор может пропустить (skip).
2. describe() — если у таблицы есть описание (комментарии колонок в
   источнике или документ-описание), SchemaEnricher переносит из него в
   паспорт названия и описания колонок. Описания нет — ничего не делается,
   LLM не вызывается.
3. summarize() — описание таблицы «что передо мной» (core/data/overview.py)
   в паспорт, после describe(): так оно видит названия колонок из документа.
"""

import logging
from dataclasses import dataclass
from uuid import uuid4

import duckdb
import openai

from app.config import settings
from app.core.data import overview
from app.core.data.connectors.base import BaseConnector
from app.core.data.profiling import TableProfile, profile_table
from app.core.data.schema_enrichment import SchemaEnricher, Superseded
from app.core.llm.client import LLMClient
from app.core.memory.profile_memory import ProfileMemory
from app.models.enrichment import ColumnEnrichment

logger = logging.getLogger(__name__)


def attach(connector: BaseConnector, db) -> dict[str, TableProfile]:
    """Подключить источник и профилировать его таблицы. Вызывать под db.lock.

    Профилирование упало — подключённые таблицы откатываются
    (connector.rollback), чтобы в DuckDB не осталось таблиц без паспорта.
    Возвращает профили подключённых таблиц без пропущенных.
    """
    tables = connector.connect(db)
    memory = ProfileMemory(db)
    profiles: dict[str, TableProfile] = {}
    try:
        for table in tables:
            try:
                profile = profile_table(db, table, approx=settings.profile_approx_quantile)
            except duckdb.Error as error:
                if connector.skip(db, table, error):
                    continue
                raise
            memory.save_profile(profile)
            profiles[table] = profile
    except BaseException:
        for table in profiles:
            memory.delete(table)
        connector.rollback(db)
        raise
    return profiles


def restore(connector: BaseConnector, db) -> list[str]:
    """Переподключить сохранённый источник после рестарта. Вызывать под db.lock.

    Таблица с паспортом не профилируется заново, если колонки представления
    совпадают с колонками паспорта; иначе (схема в источнике изменилась или
    паспорта нет) профиль пересчитывается. Нечитаемую таблицу коннектор может
    пропустить (skip), её устаревший паспорт удаляется; иной сбой профиля
    откатывает подключение (connector.rollback), как в attach().
    Возвращает подключённые таблицы без пропущенных.
    """
    tables = connector.connect(db)
    memory = ProfileMemory(db)
    restored = []
    try:
        for table in tables:
            profile = memory.load_profile(table)
            if profile is None or [column.name for column in profile.columns] != db.columns(table):
                try:
                    profile = profile_table(db, table, approx=settings.profile_approx_quantile)
                except duckdb.Error as error:
                    if connector.skip(db, table, error):
                        memory.delete(table)
                        continue
                    raise
                memory.save_profile(profile)
            restored.append(table)
    except BaseException:
        connector.rollback(db)
        raise
    return restored


@dataclass
class DescribeJob:
    """Перенос описания, запущенный подключением (mark_pending).

    run отличает его от переноса после повторного подключения тех же таблиц,
    comments — комментарии колонок, уже прочитанные mark_pending.
    """

    run: str
    comments: dict[str, dict[str, str]]


async def describe(
    connector: BaseConnector,
    db,
    tables: list[str],
    document: str | None = None,
    llm: LLMClient | None = None,
    job: DescribeJob | None = None,
) -> dict[str, list[ColumnEnrichment]]:
    """Перенести описание таблиц в паспорт; таблицы без описания пропускаются.

    document — один документ на весь источник (может описывать несколько
    таблиц). Возвращает записи паспорта по описанным таблицам. Статус описания
    таблицы — ready, если перенесены все колонки, и failed, если LLM не
    разобрала документ хотя бы по одной колонке (у неё нет fingerprint) или
    перенос упал. С job таблица, которую с тех пор подключили заново,
    пропускается: ни описания, ни статус не пишутся.
    """
    document = (document or "").strip() or None
    run = None if job is None else job.run
    enricher = None
    described = {}
    memory = ProfileMemory(db)
    for table in tables:
        comments = None if job is None else job.comments.get(table)
        if comments is None:
            with db.lock:
                comments = connector.column_comments(db, table)
        if not comments and document is None:
            continue
        enricher = enricher or SchemaEnricher(db, llm)
        try:
            rows = await enricher.enrich(table, comments=comments, document=document, run=run)
        except Superseded:
            continue
        except BaseException:
            with db.lock:
                memory.set_description_status(table, "failed", run)
            raise
        described[table] = rows
        status = "failed" if any(row.fingerprint is None for row in rows) else "ready"
        with db.lock:
            memory.set_description_status(table, status, run)
    return described


def mark_pending(
    connector: BaseConnector, db, tables: list[str], document: str | None
) -> DescribeJob:
    """Начать перенос описания до запуска describe() в фоне. Вызывать под db.lock.

    Таблицы с описанием — pending (ответ подключения уже показывает, что
    перенос идёт), остальные — none: describe() их пропустит. Перенос от
    прошлого подключения тех же таблиц ничего больше не запишет. Комментарии
    колонок читаются только без документа и передаются describe() в job.
    """
    document = (document or "").strip() or None
    job = DescribeJob(run=uuid4().hex, comments={})
    memory = ProfileMemory(db)
    for table in tables:
        if document is None:
            job.comments[table] = connector.column_comments(db, table)
        described = document is not None or bool(job.comments[table])
        memory.begin_description(table, job.run, described)
    return job


async def summarize(db, tables: list[str], llm: LLMClient | None = None) -> None:
    """Описание «что передо мной» для каждой таблицы в паспорт (overview.describe_dataset).

    Сбой одной таблицы пишется в лог, остальные описываются. LLM недоступна
    (таймаут, нет соединения) — остальные таблицы не ждут её по очереди:
    описание построится при первом вопросе.
    """
    llm = llm or LLMClient()
    for table in tables:
        try:
            await overview.describe_dataset(db, table, llm)
        except openai.APIConnectionError:
            logger.warning("LLM недоступна, описание таблиц %s отложено до вопроса", tables)
            return
        except Exception:
            logger.exception("Описание таблицы %s не построено", table)
