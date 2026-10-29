"""Идеи дашборда по таблице (#224): 4 варианта для кнопок в чате.

Генерируются только по запросу — по кнопке «Автоматическая генерация» или
когда своего запроса пользователя не хватило для плана. Кэш — в памяти
процесса по таблице и отпечатку её описания: повторное нажатие не зовёт LLM.
Когда пользователь перебрал все варианты, фронт просит новые и передаёт
названия прежних (exclude): модель их не повторяет, у такой пачки свой кэш.
"""

import hashlib
import logging
from collections.abc import Sequence

import duckdb

from app.core.data import overview, relations
from app.core.llm.client import LLMClient
from app.core.llm.prompts.dashboard_builder import DASHBOARD_IDEAS, IDEAS_EXCLUDE
from app.core.llm.prompts.system_analyst import SYSTEM_ANALYST
from app.core.memory.profile_memory import ProfileMemory
from app.models.dashboard_plan import IDEAS_COUNT, DashboardIdea, DashboardIdeas

logger = logging.getLogger(__name__)

# Описание таблицы в промптах идей и плана плиток.
TABLE_MAX_CHARS = 6000

_cache: dict[tuple[str, str, tuple[str, ...]], list[DashboardIdea]] = {}


def describe_table(db, table: str, question: str = "") -> str:
    """Таблица для промпта: факты, колонки с типами и значениями, описание, если есть.

    В конце — связанные ключами таблицы (#165): без них дашборд по заказам не
    предложит плитку «по регионам», если регионы лежат в справочнике. question
    упорядочивает их колонки.
    """
    profile = overview.passport(db, table)
    enrichment = ProfileMemory(db).load_enrichment(table)
    columns = overview.clip(
        overview.profile_lines(profile, enrichment), TABLE_MAX_CHARS, overview.COLUMN_WORDS
    )
    text = f"{overview.facts(profile)}.\n{columns}"
    summary = overview.cached_summary(db, table)
    if summary:
        text = f"{text}\n\nОписание: {summary}"
    linked = relations.describe_linked(db, table, _related(db, table, question))
    return f"{text}\n\n{linked}" if linked else text


def _related(db, table: str, question: str) -> list[relations.Related]:
    """Связанные таблицы; сбой каталога — дашборд по одной таблице."""
    try:
        return relations.related_tables(db, table, question)
    except duckdb.Error:
        logger.exception("связанные таблицы %s не прочитаны", table)
        return []


async def generate_ideas(
    db, table: str, llm: LLMClient | None = None, exclude: Sequence[str] = ()
) -> list[DashboardIdea]:
    """4 идеи дашборда, кроме exclude; пустой ответ модели — ValueError."""
    # Ключ — данные, а не текст промпта: описание и подписи колонок дописываются
    # в фоне после загрузки, и кэш не должен из-за них промахиваться.
    profile = overview.passport(db, table)
    linked = sorted(relations.component(relations.load(db), table) - {table})
    shape = f"{overview.facts(profile)} {[column.name for column in profile.columns]} {linked}"
    seen = tuple(sorted({title.strip() for title in exclude if title.strip()}))
    key = (table, hashlib.sha256(shape.encode()).hexdigest(), seen)
    if key in _cache:
        return _cache[key]
    described = describe_table(db, table)
    llm = llm or LLMClient()
    titles = "\n".join(f"- {title}" for title in seen)
    excluded = IDEAS_EXCLUDE.format(titles=titles) if seen else ""
    prompt = DASHBOARD_IDEAS.format(count=IDEAS_COUNT, table=described, exclude=excluded)
    answer = await llm.complete_structured(SYSTEM_ANALYST, prompt, DashboardIdeas)
    # Модель всё равно повторила прежний вариант — отбрасываем его
    taken = {title.casefold() for title in seen}
    fresh = (
        idea
        for idea in answer.ideas
        if idea.title.strip() and idea.title.strip().casefold() not in taken
    )
    ideas = [
        idea.model_copy(update={"id": str(number)}) for number, idea in enumerate(fresh, start=1)
    ][:IDEAS_COUNT]
    if not ideas:
        raise ValueError("Модель не предложила ни одного варианта дашборда")
    logger.info("идеи дашборда для %s: %s", table, [idea.title for idea in ideas])
    _cache[key] = ideas
    return ideas
