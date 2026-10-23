"""Обогащение паспорта: названия и описания колонок из описания таблицы.

Описание таблицы приходит из двух мест:
- комментарии колонок в источнике (BaseConnector.column_comments —
  PostgreSQL COMMENT ON COLUMN, ClickHouse) идут в паспорт как есть, без LLM;
- документ-описание, приложенный при подключении (README, словарь
  колонок), LLM разбирает в название и описание каждой колонки — только
  то, что в нём написано.

Нет ни того ни другого — энричер ничего не делает: паспорт остаётся
профилем таблицы, который строит ingest.attach сразу после подключения.
Профиль энричер не считает и ничего, кроме названий и описаний, не пишет.
Колонка, у которой не изменились имя, тип, комментарий и документ
(fingerprint), повторно не обрабатывается. Без документа (повторное
подключение источника) сравнивается только комментарий: названия и
описания из прошлого документа сохраняются.
"""

import hashlib
import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass

import openai

from app.config import settings
from app.core.data.profiling import ColumnStats
from app.core.llm.client import LLMClient
from app.core.llm.prompts.column_extract import COLUMN_EXTRACT
from app.core.memory.profile_memory import ProfileMemory
from app.models.enrichment import ColumnEnrichment, EnrichmentBatch

logger = logging.getLogger(__name__)

SYSTEM = "Ты — аналитик данных. Отвечай только JSON."
TITLE_MAX_CHARS = 40


def fingerprint(column: ColumnStats, comment: str | None, document: str | None) -> str:
    """Хеш всего, от чего зависят название и описание колонки."""
    digest = hashlib.sha256(document.encode()).hexdigest() if document else None
    payload = json.dumps([column.name, column.dtype, comment, digest], ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def source_comment(name: str, comment: str | None) -> str | None:
    """Комментарий из источника; пустой или равный имени колонки — не описание."""
    text = (comment or "").strip()
    if not text or text.casefold() == name.casefold():
        return None
    return comment


def previous_comment(record: ColumnEnrichment) -> str | None:
    """Комментарий источника, с которым колонка была обработана в прошлый раз."""
    return record.description if record.description_source == "source" else None


def batches(items: list, size: int) -> Iterator[list]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


@dataclass
class Draft:
    """Колонка, которую энричинг обрабатывает в этом запуске."""

    column: ColumnStats
    comment: str | None
    fingerprint: str
    title: str | None = None
    description: str | None = None
    failed: bool = False

    def __post_init__(self) -> None:
        self.description = self.comment

    @property
    def name(self) -> str:
        return self.column.name

    def context(self) -> dict:
        """Колонка для промпта: имя, тип и комментарий из источника (подсказка)."""
        return {"column": self.name, "type": self.column.dtype, "comment": self.comment}

    def accept(self, answer: ColumnEnrichment) -> None:
        """Принять ответ LLM. Пустое или слишком длинное значение не берётся:
        документ мог не описывать колонку. Комментарий источника важнее документа.
        """
        title = (answer.title or "").strip()
        if title and len(title) <= TITLE_MAX_CHARS and title.casefold() != self.name.casefold():
            self.title = title
        elif title:
            logger.warning("LLM вернула негодное название колонки «%s»: %r", self.name, title)
        if self.comment is not None:
            return
        text = (answer.description or "").strip()
        if text and len(text) <= settings.enrich_description_max_chars:
            self.description = text
        elif text:
            logger.warning(
                "LLM вернула описание колонки «%s» длиннее %d символов",
                self.name,
                settings.enrich_description_max_chars,
            )

    def record(self) -> ColumnEnrichment:
        """Запись паспорта. При отказе LLM — только комментарий источника и без
        fingerprint, чтобы при следующем запуске колонка снова ушла в LLM.
        """
        if self.failed:
            return ColumnEnrichment(
                column=self.name,
                description=self.comment,
                description_source="none" if self.comment is None else "source",
            )
        if self.comment is not None:
            source = "source"
        else:
            source = "none" if self.description is None else "document"
        return ColumnEnrichment(
            column=self.name,
            title=self.title,
            description=self.description,
            description_source=source,
            fingerprint=self.fingerprint,
        )


class SchemaEnricher:
    """Перенос описания таблицы (комментарии, документ) в паспорт."""

    def __init__(self, db, llm: LLMClient | None = None) -> None:
        """db — экземпляр DuckDBManager."""
        self.db = db
        self.llm = llm or LLMClient()
        self.memory = ProfileMemory(db)
        self.llm_available = True

    async def enrich(
        self, table: str, comments: dict[str, str] | None = None, document: str | None = None
    ) -> list[ColumnEnrichment]:
        """Записать в паспорт названия и описания колонок из описания таблицы.

        comments — описания колонок из источника (BaseConnector.column_comments),
        document — текст документа-описания. Без них ничего не меняется.
        LLM вызывается только для документа и вне db.lock: на время ответа
        модели DuckDB не блокируется. Возвращает записи всех колонок в
        порядке таблицы.

        Raises:
            LookupError: профиля таблицы нет — ingest.attach ещё не вызывался.
        """
        document = (document or "").strip() or None
        with self.db.lock:
            profile = self.memory.load_profile(table)
            saved = {row.column: row for row in self.memory.load_enrichment(table)}
        if profile is None:
            raise LookupError(f"Нет профиля таблицы «{table}»: сначала ingest.attach().")
        comments = {
            name: comment
            for name, comment in (comments or {}).items()
            if source_comment(name, comment) is not None
        }
        if not comments and document is None:
            return [saved[column.name] for column in profile.columns]

        drafts = []
        for column in profile.columns:
            comment = comments.get(column.name)
            record = saved[column.name]
            if document is None:
                # Без документа меняется только комментарий: название из
                # документа остаётся, описание уступает новому комментарию.
                if record.fingerprint is None or previous_comment(record) != comment:
                    current = fingerprint(column, comment, None)
                    drafts.append(Draft(column, comment, current, title=record.title))
                continue
            current = fingerprint(column, comment, document)
            if record.fingerprint != current:
                drafts.append(Draft(column, comment, current))
        self.llm_available = True
        if document is not None:
            await self.extract(drafts, document)
        fresh = {draft.name: draft.record() for draft in drafts}
        self.memory.save_enrichment(table, list(fresh.values()))
        return [fresh.get(column.name, saved[column.name]) for column in profile.columns]

    async def extract(self, drafts: list[Draft], document: str) -> None:
        """Названия и описания колонок из документа (промпт column_extract)."""
        limit = settings.enrich_document_max_chars
        if len(document) > limit:
            logger.warning("Документ-описание длиннее %d символов обрезан", limit)
            document = document[:limit]
        for group in batches(drafts, settings.enrich_batch_size):
            prompt = COLUMN_EXTRACT.format(
                title_max_chars=TITLE_MAX_CHARS,
                max_chars=settings.enrich_description_max_chars,
                document=document,
                columns=json.dumps([draft.context() for draft in group], ensure_ascii=False),
            )
            answers = await self.ask(prompt, group)
            for draft in group:
                if not draft.failed:
                    draft.accept(answers[draft.name])

    async def ask(self, prompt: str, group: list[Draft]) -> dict[str, ColumnEnrichment]:
        """Ответ LLM по колонкам пачки; колонки без ответа помечаются отказом."""
        names = [draft.name for draft in group]
        batch = await self.request(prompt, names)
        answers: dict[str, ColumnEnrichment] = {}
        for entry in batch.columns if batch else []:
            if entry.column in names:
                answers.setdefault(entry.column, entry)
            else:
                logger.warning("LLM вернула незапрошенную колонку «%s»", entry.column)
        missing = [name for name in names if name not in answers]
        if batch and missing:
            logger.warning("LLM не вернула колонки %s", missing)
        for draft in group:
            draft.failed = draft.failed or draft.name in missing
        return answers

    async def request(self, prompt: str, names: list[str]) -> EnrichmentBatch | None:
        """Ответ LLM или None при отказе.

        После таймаута или ошибки соединения LLM до конца запуска больше не
        вызывается, иначе каждая пачка ждала бы LLM_TIMEOUT_S с ретраями.
        Невалидный ответ (JSON, схема), ошибка API или другое исключение клиента
        (например, ответ без choices) — отказ только этой пачки; неожиданное
        исключение пишется в лог с traceback. Отмена (CancelledError) не глотается.
        """
        if not self.llm_available:
            logger.warning("LLM недоступна: колонки %s сохранены без документа", names)
            return None
        try:
            return await self.llm.complete_structured(SYSTEM, prompt, EnrichmentBatch)
        except openai.APIConnectionError as error:
            self.llm_available = False
            logger.warning("LLM недоступна (%s): колонки %s сохранены без документа", error, names)
        except (ValueError, openai.OpenAIError) as error:
            logger.warning("Невалидный ответ LLM для колонок %s: %s", names, error)
        except Exception:
            logger.exception("Неожиданная ошибка LLM для колонок %s", names)
        return None
