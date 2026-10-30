"""Близость вопроса к таблицам по смыслу (эмбеддинги, EMBED_MODEL).

Слова вопроса не находят синонимов: «погода» не совпадает с таблицей
«Температура по дням» ни именем, ни колонками. Эмбеддинг вопроса сравнивается
с эмбеддингом описания каждой таблицы: имя, название (#211), описание и колонки.

Векторы таблиц считаются, когда понадобились, одним запросом на все новые и
хранятся в _meta.table_vector по отпечатку текста: таблица с новым названием
или описанием пересчитывается, остальные — нет. На вопрос — один запрос.
EMBED_MODEL не задана или сервер не ответил — пустой словарь, и выбор таблицы
идёт только по словам (relations).
"""

import hashlib
import logging
import math
import time

from openai import AsyncOpenAI

from app.config import settings
from app.core.memory.profile_memory import ProfileMemory

logger = logging.getLogger(__name__)

SCHEMA = (
    "CREATE SCHEMA IF NOT EXISTS _meta",
    """CREATE TABLE IF NOT EXISTS _meta.table_vector (
        table_name   VARCHAR PRIMARY KEY,
        model        VARCHAR NOT NULL,
        fingerprint  VARCHAR NOT NULL,
        vector       DOUBLE[] NOT NULL
    )""",
)
# Описание таблицы в тексте для эмбеддинга — первый абзац, без подробностей.
SUMMARY_CHARS = 600
# Сервер эмбеддингов не ответил — столько секунд не спрашиваем, выбор по словам.
RETRY_S = 600

_down_until = 0.0


def table_text(db, table: str) -> str:
    """Чем таблица является для поиска по смыслу: имя, название, описание, колонки."""
    memory = ProfileMemory(db)
    title = memory.load_display_name(table)
    saved = memory.load_summary(table)
    summary = saved[0].split("\n\n")[0][:SUMMARY_CHARS] if saved else ""
    titles = {row.column: row.title for row in memory.load_enrichment(table) if row.title}
    columns = ", ".join(
        f"{name} ({titles[name]})" if name in titles else name for name in db.columns(table)
    )
    parts = [table.replace("_", " "), title or "", summary, f"Колонки: {columns}"]
    return "\n".join(part for part in parts if part)


def cosine(left: list[float], right: list[float]) -> float:
    norm = math.sqrt(sum(x * x for x in left) * sum(y * y for y in right))
    return sum(x * y for x, y in zip(left, right, strict=True)) / norm if norm else 0.0


class TableVectors:
    """Эмбеддинги таблиц и вопроса; client — для тестов (AsyncOpenAI.embeddings)."""

    def __init__(self, db, client=None) -> None:
        self.db = db
        self.model = settings.embed_model
        self._client = client

    @property
    def client(self):
        if self._client is None:
            key = settings.embed_api_key or settings.llm_api_key
            self._client = AsyncOpenAI(
                base_url=settings.embed_base_url or settings.llm_base_url,
                api_key=key.get_secret_value(),
                timeout=settings.embed_timeout_s,
                max_retries=1,
            )
        return self._client

    async def similarity(self, question: str) -> dict[str, float]:
        """Близость вопроса к каждой таблице; без модели или при сбое — пусто."""
        global _down_until
        if not self.model or time.monotonic() < _down_until:
            return {}
        try:
            vectors = await self._tables()
            if not vectors:
                return {}
            [asked] = await self._embed([question])
        except Exception:
            logger.warning(
                "эмбеддинги не получены, %d с выбор таблицы только по словам",
                RETRY_S,
                exc_info=True,
            )
            _down_until = time.monotonic() + RETRY_S
            return {}
        return {table: cosine(asked, vector) for table, vector in vectors.items()}

    async def _tables(self) -> dict[str, list[float]]:
        tables = self.db.user_tables()
        texts = {table: table_text(self.db, table) for table in tables}
        prints = {table: _fingerprint(text) for table, text in texts.items()}
        for statement in SCHEMA:
            self.db.execute(statement)
        rows = self.db.query(
            "SELECT table_name, fingerprint, vector FROM _meta.table_vector WHERE model = $model",
            {"model": self.model},
        )
        vectors = {
            row["table_name"]: row["vector"]
            for row in rows
            if prints.get(row["table_name"]) == row["fingerprint"]
        }
        missing = [table for table in tables if table not in vectors]
        if missing:
            logger.info("эмбеддинги таблиц: %s", missing)
            for table, vector in zip(
                missing, await self._embed([texts[t] for t in missing]), strict=True
            ):
                vectors[table] = vector
                self.db.execute(
                    "INSERT OR REPLACE INTO _meta.table_vector VALUES ($table, $model, $print, $vector)",
                    {"table": table, "model": self.model, "print": prints[table], "vector": vector},
                )
        return vectors

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        answer = await self.client.embeddings.create(model=self.model, input=texts)
        return [item.embedding for item in sorted(answer.data, key=lambda item: item.index)]


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
