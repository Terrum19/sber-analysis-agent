"""Обогащение схемы: описания и алиасы колонок (память уровня 3).

Профиль таблицы и описания колонок из источника превращаются в паспорт
данных: короткое описание из источника остаётся как есть, длинное
сжимается LLM, отсутствующее — генерируется; на каждую колонку — до 4
человекочитаемых алиасов, т.к. система рассчитана на не-разработчика.
Учитывает бизнес-словарь пользователя (если есть). Колонка, у которой не
изменились имя, тип, описание в источнике и словарь (fingerprint), в LLM
повторно не отправляется.
"""

import hashlib
import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field

import openai

from app.config import settings
from app.core.data.profiling import ColumnStats, profile_table, to_json
from app.core.llm.client import LLMClient
from app.core.llm.prompts.alias_gen import ALIAS_GEN
from app.core.llm.prompts.description_compress import DESCRIPTION_COMPRESS
from app.core.memory.profile_memory import ProfileMemory
from app.models.enrichment import ColumnEnrichment, EnrichmentBatch

logger = logging.getLogger(__name__)

SYSTEM = "Ты — аналитик данных. Отвечай только JSON."
MAX_ALIASES = 4
ALIAS_MAX_CHARS = 40
TOP_VALUES_IN_PROMPT = 5
VALUE_PREVIEW_CHARS = 60


def fingerprint(column: ColumnStats, comment: str | None, glossary: dict | None) -> str:
    """Хеш всего, от чего зависят описание и алиасы колонки."""
    payload = json.dumps(
        [column.name, column.dtype, comment, glossary], ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def description_source(name: str, comment: str | None) -> str:
    """Откуда возьмётся описание: source / source_compressed / generated."""
    text = (comment or "").strip()
    if not text or text.casefold() == name.casefold():
        return "generated"
    if len(comment) <= settings.enrich_description_max_chars:
        return "source"
    return "source_compressed"


def clean_aliases(name: str, aliases: list[str]) -> list[str]:
    """Без пустых, дублей (без учёта регистра), имени колонки и слишком длинных."""
    seen = {name.casefold()}
    result = []
    for alias in aliases:
        alias = alias.strip()
        key = alias.casefold()
        if not alias or key in seen or len(alias) > ALIAS_MAX_CHARS:
            continue
        seen.add(key)
        result.append(alias)
    return result[:MAX_ALIASES]


def preview(value: object) -> object:
    return value[:VALUE_PREVIEW_CHARS] if isinstance(value, str) else value


def batches(items: list, size: int) -> Iterator[list]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


@dataclass
class Draft:
    """Колонка, которую энричинг обрабатывает в этом запуске."""

    column: ColumnStats
    comment: str | None
    fingerprint: str
    source: str
    description: str | None = None
    aliases: list[str] = field(default_factory=list)
    failed: bool = False

    @property
    def name(self) -> str:
        return self.column.name

    def context(self) -> dict:
        """Колонка для промпта: description = None — описание нужно сгенерировать."""
        column = self.column
        return to_json(
            {
                "column": column.name,
                "type": column.dtype,
                "kind": column.kind,
                "description": self.description,
                "null_pct": column.null_pct,
                "min": preview(column.min),
                "max": preview(column.max),
                "top_values": [
                    preview(entry["value"]) for entry in column.top_values[:TOP_VALUES_IN_PROMPT]
                ],
            }
        )

    def accept(self, text: str | None) -> None:
        """Принять описание от LLM: непустое и не длиннее порога, иначе — отказ."""
        text = (text or "").strip()
        if text and len(text) <= settings.enrich_description_max_chars:
            self.description = text
            return
        self.failed = True
        logger.warning(
            "LLM вернула пустое или длиннее %d символов описание колонки «%s»",
            settings.enrich_description_max_chars,
            self.name,
        )

    def record(self) -> ColumnEnrichment:
        """Запись паспорта. При отказе LLM — описание из источника, без алиасов
        и без fingerprint, чтобы при следующем запуске колонка снова ушла в LLM.
        """
        if not self.failed:
            return ColumnEnrichment(
                column=self.name,
                description=self.description,
                aliases=self.aliases,
                description_original=self.comment,
                description_source=self.source,
                fingerprint=self.fingerprint,
            )
        description = None if self.source == "generated" else self.comment
        return ColumnEnrichment(
            column=self.name,
            description=description,
            aliases=[],
            description_original=self.comment,
            description_source="none" if description is None else "source",
        )


class SchemaEnricher:
    """Генератор описаний и алиасов колонок."""

    def __init__(self, db, llm: LLMClient | None = None) -> None:
        """db — экземпляр DuckDBManager."""
        self.db = db
        self.llm = llm or LLMClient()
        self.memory = ProfileMemory(db)
        self.llm_available = True

    async def enrich(
        self, table: str, comments: dict[str, str] | None = None, glossary: dict | None = None
    ) -> list[ColumnEnrichment]:
        """Профилировать таблицу, описать колонки и сохранить паспорт в ProfileMemory.

        comments — описания колонок из источника (BaseConnector.column_comments).
        glossary=None оставляет словарь, сохранённый с паспортом ранее.
        LLM вызывается вне db.lock: на время ответа модели DuckDB не блокируется.
        Возвращает описания и алиасы всех колонок в порядке таблицы.
        """
        comments = comments or {}
        with self.db.lock:
            profile = profile_table(self.db, table, approx=settings.profile_approx_quantile)
            self.memory.save_profile(profile, glossary)
            glossary = self.memory.load_glossary(table)
            saved = {row.column: row for row in self.memory.load_enrichment(table)}
        drafts = []
        for column in profile.columns:
            comment = comments.get(column.name)
            current = fingerprint(column, comment, glossary)
            if saved[column.name].fingerprint == current:
                continue
            source = description_source(column.name, comment)
            description = comment if source == "source" else None
            drafts.append(Draft(column, comment, current, source, description))
        self.llm_available = True
        await self.compress([draft for draft in drafts if draft.source == "source_compressed"])
        await self.describe([draft for draft in drafts if not draft.failed], glossary)
        fresh = {draft.name: draft.record() for draft in drafts}
        self.memory.save_enrichment(table, list(fresh.values()))
        return [fresh.get(column.name, saved[column.name]) for column in profile.columns]

    async def compress(self, drafts: list[Draft]) -> None:
        """Сжать длинные описания из источника (шаг 2, source_compressed)."""
        for group in batches(drafts, settings.enrich_batch_size):
            columns = [{"column": draft.name, "description": draft.comment} for draft in group]
            prompt = DESCRIPTION_COMPRESS.format(
                max_chars=settings.enrich_description_max_chars,
                columns=json.dumps(columns, ensure_ascii=False, indent=1),
            )
            answers = await self.ask(prompt, group)
            for draft in group:
                if not draft.failed:
                    draft.accept(answers[draft.name].description)

    async def describe(self, drafts: list[Draft], glossary: dict | None) -> None:
        """Сгенерировать недостающие описания (шаг 2) и алиасы (шаг 3)."""
        for group in batches(drafts, settings.enrich_batch_size):
            prompt = ALIAS_GEN.format(
                max_aliases=MAX_ALIASES,
                alias_max_chars=ALIAS_MAX_CHARS,
                max_chars=settings.enrich_description_max_chars,
                columns=json.dumps(
                    [draft.context() for draft in group], ensure_ascii=False, indent=1
                ),
                glossary=json.dumps(glossary or {}, ensure_ascii=False),
            )
            answers = await self.ask(prompt, group)
            for draft in group:
                if draft.failed:
                    continue
                answer = answers[draft.name]
                if draft.source == "generated":
                    draft.accept(answer.description)
                draft.aliases = clean_aliases(draft.name, answer.aliases)

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
            logger.warning("LLM недоступна: колонки %s сохранены без энричинга", names)
            return None
        try:
            return await self.llm.complete_structured(SYSTEM, prompt, EnrichmentBatch)
        except openai.APIConnectionError as error:
            self.llm_available = False
            logger.warning("LLM недоступна (%s): колонки %s сохранены без энричинга", error, names)
        except (ValueError, openai.OpenAIError) as error:
            logger.warning("Невалидный ответ LLM для колонок %s: %s", names, error)
        except Exception:
            logger.exception("Неожиданная ошибка LLM для колонок %s", names)
        return None
