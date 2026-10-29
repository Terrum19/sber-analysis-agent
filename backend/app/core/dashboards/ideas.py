"""Идеи дашборда по таблице (#224): 4 варианта для кнопок в чате.

Генерируются только по запросу — по кнопке «Автоматическая генерация» или
когда своего запроса пользователя не хватило для плана. Кэш — в памяти
процесса по таблице и отпечатку её описания: повторное нажатие не зовёт LLM.
"""

import hashlib
import logging

from app.core.data import overview
from app.core.llm.client import LLMClient
from app.core.llm.prompts.dashboard_builder import DASHBOARD_IDEAS
from app.core.llm.prompts.system_analyst import SYSTEM_ANALYST
from app.core.memory.profile_memory import ProfileMemory
from app.models.dashboard_plan import IDEAS_COUNT, DashboardIdea, DashboardIdeas

logger = logging.getLogger(__name__)

# Описание таблицы в промптах идей и плана плиток.
TABLE_MAX_CHARS = 6000

_cache: dict[tuple[str, str], list[DashboardIdea]] = {}


def describe_table(db, table: str) -> str:
    """Таблица для промпта: факты, колонки с типами и значениями, описание, если есть."""
    profile = overview.passport(db, table)
    enrichment = ProfileMemory(db).load_enrichment(table)
    columns = overview.clip(
        overview.profile_lines(profile, enrichment), TABLE_MAX_CHARS, overview.COLUMN_WORDS
    )
    text = f"{overview.facts(profile)}.\n{columns}"
    summary = overview.cached_summary(db, table)
    return f"{text}\n\nОписание: {summary}" if summary else text


async def generate_ideas(db, table: str, llm: LLMClient | None = None) -> list[DashboardIdea]:
    """4 идеи дашборда; пустой или короткий ответ модели — ValueError."""
    # Ключ — данные, а не текст промпта: описание и подписи колонок дописываются
    # в фоне после загрузки, и кэш не должен из-за них промахиваться.
    profile = overview.passport(db, table)
    shape = f"{overview.facts(profile)} {[column.name for column in profile.columns]}"
    key = (table, hashlib.sha256(shape.encode()).hexdigest())
    if key in _cache:
        return _cache[key]
    described = describe_table(db, table)
    llm = llm or LLMClient()
    prompt = DASHBOARD_IDEAS.format(count=IDEAS_COUNT, table=described)
    answer = await llm.complete_structured(SYSTEM_ANALYST, prompt, DashboardIdeas)
    ideas = [
        idea.model_copy(update={"id": str(number)})
        for number, idea in enumerate(
            (idea for idea in answer.ideas if idea.title.strip()), start=1
        )
    ][:IDEAS_COUNT]
    if not ideas:
        raise ValueError("Модель не предложила ни одного варианта дашборда")
    logger.info("идеи дашборда для %s: %s", table, [idea.title for idea in ideas])
    _cache[key] = ideas
    return ideas
