"""Обогащение схемы: алиасы и описания колонок (память уровня 3).

Из таблицы извлекается схема и отдаётся LLM для генерации до 4
человекочитаемых алиасов на колонку — т.к. система рассчитана на
не-разработчика. Учитывает бизнес-словарь пользователя (если есть).
"""

from app.core.data.profiling import profile_table
from app.core.llm.client import LLMClient
from app.core.llm.prompts.alias_gen import ALIAS_GEN
from app.core.memory.profile_memory import ProfileMemory


class SchemaEnricher:
    """Генератор алиасов и описаний колонок."""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or LLMClient()

    async def enrich(self, db, table: str, glossary: dict | None = None) -> dict:
        """Профилировать таблицу и сгенерировать алиасы колонок.

        Результат сохраняется в ProfileMemory («паспорт данных»),
        чтобы не сканировать источник заново на каждый вопрос.
        """
        profile = profile_table(db, table)
        prompt = ALIAS_GEN.format(schema=profile, glossary=glossary or {})
        aliases = await self.llm.complete("Ты — аналитик данных.", prompt)
        passport = {"profile": profile, "aliases_raw": aliases, "glossary": glossary}
        ProfileMemory().save(table, passport)
        return passport
