"""Прототип исправлений качества (docs/reports/llm_perf, H-Q1, H-Q2, H-T1, H-T2).

Включается settings.agent_quality_fixes; по умолчанию поведение агента прежнее.
Найдено сверкой прогона V1 с эталоном (experiment_log.md, П3):

- H-Q1: инсайт видел первые 50 строк результата без их общего числа и
  «считал» по выборке -> факты (число строк, итоги числовых колонок)
  считает SQL и передаёт модели готовыми;
- H-Q2: заголовок инсайта без ответа -> в headline обязателен ответ числом;
- H-T1: `aggregate` — это частоты значений, а итога по всей таблице в
  реестре нет -> group_by с by=[] даёт один итог, описания тулов уточнены;
- H-T2: «по месяцам» планировалось как group_by по дате -> правило и
  пример с time_bucket.
"""

import re

from app.core.data.duckdb_manager import DuckDBManager

# Дополнения к описаниям тулов для планировщика.
TOOL_HINTS = {
    "aggregate": (
        " (это НЕ сумма и НЕ среднее: только сколько раз встречается каждое значение;"
        " итог по всей таблице — group_by с by=[])"
    ),
    "group_by": " by=[] — одна строка с итогом по всей таблице (общая сумма, среднее, число строк)",
    "time_bucket": " Используй для любых «по дням/неделям/месяцам/кварталам/годам»",
}

PLANNER_RULES = """
Дополнительные правила:
- «по месяцам», «по неделям», «по дням», «динамика» — time_bucket (interval=...),
  а не group_by по колонке даты: group_by по дате даёт строку на каждый день.
- «сколько событий/строк» — function=COUNT; «сумма» — SUM; «средняя» — AVG.
- Один итог по всей таблице («общая сумма», «сколько всего») — group_by с by=[].
- Усилия по вопросу: столько шагов, сколько нужно для ответа. Вопрос одним
  числом — один шаг и final_viz=kpi. Стат-тесты, прогноз, разведку и дашборд
  добавляй, только если вопрос о них просит.
- Пример: «сколько событий по месяцам» ->
  {"tool": "time_bucket", "arguments": {"date_column": "event_date", "interval": "month", "function": "COUNT"}}
"""

INSIGHT_RULES = """
Факты о результате (посчитаны по всей таблице, им доверяй больше, чем показанным строкам):
{facts}

- headline должен содержать сам ответ на вопрос числом (например «Выручка за квартал — 12 345»).
- Не пересчитывай строки вручную: если показаны не все строки, используй факты выше.
"""

PAST_CONTEXT = (
    "шаги ПРОШЛЫХ вопросов этой сессии (только для справки: не повторяй их "
    "фильтры и шаги, если текущий вопрос явно не продолжает предыдущий — "
    "«а теперь», «из них», «там же»): {summary}"
)

NUMBER_FIX = """

В headline твоего ответа число {numbers}, которого нет ни в фактах, ни в результате.
Перепиши ответ, беря числа только из фактов и результата."""

_NUMBER = re.compile(r"\d[\d\s]*(?:[.,]\d+)?")


# H-C1b: признаки того, что вопрос продолжает предыдущий.
FOLLOW_UP = re.compile(
    r"\b(из них|из этих|среди них|а теперь|теперь|там же|то же|этих|эти|этот|этого|"
    r"дальше|ещё|еще|а если|а по|а для|предыдущ|прошл)\w*",
    re.IGNORECASE,
)


def frame_context(summary: str, question: str = "") -> str:
    """H-C1/H-C1b: прошлые шаги — только для вопроса-продолжения и с пометкой «справка».

    Инструкция «не повторяй» в V4 не помогла (модель всё равно копировала
    фильтр), поэтому самостоятельному вопросу прошлые шаги не показываются вовсе.
    """
    if not summary or not FOLLOW_UP.search(question):
        return "нет"
    return PAST_CONTEXT.format(summary=summary)


def ungrounded_numbers(headline: str, prompt: str) -> list[str]:
    """H-Q3: числа заголовка, которых нет в промпте (факты + результат)."""
    known = {_norm(m) for m in _NUMBER.findall(prompt)}
    return [m.strip() for m in _NUMBER.findall(headline) if _norm(m) not in known]


def _norm(number: str) -> str:
    text = re.sub(r"\s", "", number).replace(",", ".")
    try:
        value = float(text)
    except ValueError:
        return text
    return str(round(value, 2))


# H-R1: ядро тулов, нужное почти любому вопросу; остальные — по словам вопроса.
CORE_TOOLS = (
    "filter_rows",
    "group_by",
    "time_bucket",
    "sort_limit",
    "aggregate",
    "create_column",
    "select_columns",
    "describe_stats",
    "search_categorical_values",
)
STEM_LENGTH = 5


def select_tools(question: str) -> str:
    """Описания тулов для промпта: ядро + тулы, чьё имя/описание пересекается с вопросом."""
    from app.core.tools.registry import tool_registry

    stems = {w.lower()[:STEM_LENGTH] for w in re.findall(r"\w{4,}", question)}
    lines = []
    for line in tool_registry.describe().split("\n"):
        name = line[2:].split(" ", 1)[0]
        words = {w.lower()[:STEM_LENGTH] for w in re.findall(r"\w{4,}", line.split(";", 1)[0])}
        if name in CORE_TOOLS or stems & words:
            lines.append(line)
    return "\n".join(lines)


_NUMERIC = ("INT", "DECIMAL", "DOUBLE", "FLOAT", "REAL", "NUMERIC", "HUGEINT")


def insight_facts(db: DuckDBManager, table: str, shown: int) -> str:
    """Число строк и итоги числовых колонок результата — посчитанные SQL, а не LLM."""
    total = db.query(f'SELECT COUNT(*) AS n FROM "{table}"')[0]["n"]
    lines = [f"- строк в результате: {total} (показано модели: {min(total, shown)})"]
    for col in db.query(f'DESCRIBE "{table}"'):
        name, kind = col["column_name"], str(col["column_type"]).upper()
        if not any(token in kind for token in _NUMERIC):
            continue
        row = db.query(
            f'SELECT SUM("{name}") AS s, AVG("{name}") AS a, MIN("{name}") AS lo, '
            f'MAX("{name}") AS hi FROM "{table}"'
        )[0]
        lines.append(
            f"- {name}: сумма {row['s']}, среднее {_round(row['a'])}, "
            f"мин {row['lo']}, макс {row['hi']}"
        )
    return "\n".join(lines)


def _round(value):
    return round(value, 2) if isinstance(value, float) else value
