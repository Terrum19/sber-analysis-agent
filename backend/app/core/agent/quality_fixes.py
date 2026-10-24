"""Исправления качества плана и инсайта (docs/reports/llm_perf, H-Q1..H-Q3, H-T1, H-T2, H-C1).

Включаются settings.agent_quality_fixes (по умолчанию включены); тексты
правил — в app/core/llm/prompts/quality_rules.py. Найдено сверкой прогона
V1 с эталоном (experiment_log.md, П3):

- H-Q1: инсайт видел первые 50 строк результата без их общего числа и
  «считал» по выборке -> факты (число строк, итоги числовых колонок)
  считает SQL и передаёт модели готовыми;
- H-Q2: заголовок инсайта без ответа -> в headline обязателен ответ числом;
- H-Q3: число в заголовке, которого нет ни в фактах, ни в результате ->
  один переспрос;
- H-T1: `aggregate` — это частоты значений, а итога по всей таблице в
  реестре нет -> group_by с by=[] даёт один итог, описания тулов уточнены;
- H-T2: «по месяцам» планировалось как group_by по дате -> правило и
  пример с time_bucket;
- H-C1: модель копировала фильтры прошлых вопросов -> прошлые шаги видит
  только вопрос-продолжение.
"""

import re

from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.prompts.quality_rules import PAST_CONTEXT
from app.core.tools.base import identifier
from app.core.tools.registry import tool_registry

# Число: «12345», «12 345» (разряды через пробел), «12,5», «12.5».
_NUMBER = re.compile(r"\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")

# H-C1b: признаки того, что вопрос продолжает предыдущий. Вопрос с «а»/«и» в
# начале («А в Москве?», «И по регионам?») — почти всегда продолжение;
# «прошлый»/«предыдущий» сами по себе — нет («за прошлый месяц»).
FOLLOW_UP = re.compile(
    r"^\s*[аи]\s|\b(?:из них|из этих|среди них|теперь|там же|то же|эти\w*|этот|этого|"
    r"дальше|ещё|еще|предыдущ\w* (?:вопрос|ответ|результат|шаг))",
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


def ungrounded_numbers(headline: str, sources: str) -> list[str]:
    """H-Q3: числа заголовка, которых нет в sources (вопрос, результат, факты).

    Округление допускается до точности, с которой число написано в заголовке
    («12.3» опирается на 12.34). Проценты не проверяются: доли модель считает
    сама, в фактах их нет.
    """
    known = [_value(m) for m in _NUMBER.findall(sources)]
    missing = []
    for match in _NUMBER.finditer(headline):
        if headline[match.end() :].lstrip().startswith("%"):
            continue
        text = match.group()
        value, tolerance = _value(text), _tolerance(text)
        if not any(abs(value - other) <= tolerance for other in known):
            missing.append(text)
    return missing


def _value(number: str) -> float:
    return float(re.sub(r"\s", "", number).replace(",", "."))


def _tolerance(number: str) -> float:
    """Половина последнего разряда: «12» — 0.5, «12.3» — 0.05."""
    decimals = len(re.split(r"[.,]", number)[1]) if re.search(r"[.,]", number) else 0
    return 0.5 * 10**-decimals + 1e-9


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
    stems = {w.lower()[:STEM_LENGTH] for w in re.findall(r"\w{4,}", question)}
    lines = []
    for line in tool_registry.describe().split("\n"):
        name = line[2:].split(" ", 1)[0]
        words = {w.lower()[:STEM_LENGTH] for w in re.findall(r"\w{4,}", line.split(";", 1)[0])}
        if name in CORE_TOOLS or stems & words:
            lines.append(line)
    return "\n".join(lines)


# Типы DuckDB, у которых осмысленны сумма и среднее (INTERVAL сюда не входит).
_NUMERIC = {
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "UHUGEINT",
    "DECIMAL",
    "DOUBLE",
    "FLOAT",
    "REAL",
}


def insight_facts(db: DuckDBManager, table: str, shown: int) -> str:
    """Число строк и итоги числовых колонок результата — посчитанные SQL, а не LLM."""
    numeric = [
        col["column_name"]
        for col in db.query(f"DESCRIBE {identifier(table)}")
        if str(col["column_type"]).upper().split("(")[0] in _NUMERIC
    ]
    metrics = ["COUNT(*) AS n"]
    for i, name in enumerate(numeric):
        column = identifier(name)
        metrics += [
            f"SUM({column}) AS s{i}",
            f"AVG({column}) AS a{i}",
            f"MIN({column}) AS lo{i}",
            f"MAX({column}) AS hi{i}",
        ]
    row = db.query(f"SELECT {', '.join(metrics)} FROM {identifier(table)}")[0]
    lines = [f"- строк в результате: {row['n']} (показано модели: {min(row['n'], shown)})"]
    lines += [
        f"- {name}: сумма {row[f's{i}']}, среднее {_round(row[f'a{i}'])}, "
        f"мин {row[f'lo{i}']}, макс {row[f'hi{i}']}"
        for i, name in enumerate(numeric)
    ]
    return "\n".join(lines)


def _round(value):
    return round(value, 2) if isinstance(value, float) else value
