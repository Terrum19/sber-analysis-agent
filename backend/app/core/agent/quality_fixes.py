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
  пример с time_bucket.

H-C1 (прошлые шаги видел только вопрос-продолжение по regex) заменён
прошлыми ходами в промпте с правилом «самостоятельному вопросу фильтры
не переноси» (#163).
"""

import re
from datetime import date, datetime

from app.core.data.duckdb_manager import DuckDBManager
from app.core.tools.base import identifier
from app.core.tools.registry import tool_registry
from app.models.research_step import ResearchStep

# Число: «12345», «12 345» (разряды через пробел), «12,5», «12.5».
_NUMBER = re.compile(r"\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")


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
    "read_history",
    "join_related",
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
    """Число строк и итоги числовых колонок результата — посчитанные SQL, а не LLM.

    У минимума и максимума подписана строка, где они достигаются (первая
    нечисловая колонка: период, регион, id): «лучший месяц» читается из
    фактов, а не поиском по строкам.
    """
    columns = db.query(f"DESCRIBE {identifier(table)}")
    numeric = [
        col["column_name"]
        for col in columns
        if str(col["column_type"]).upper().split("(")[0] in _NUMERIC
    ]
    label = next((c["column_name"] for c in columns if c["column_name"] not in numeric), None)
    metrics = ["COUNT(*) AS n"]
    for i, name in enumerate(numeric):
        column = identifier(name)
        metrics += [
            f"SUM({column}) AS s{i}",
            f"AVG({column}) AS a{i}",
            f"MIN({column}) AS lo{i}",
            f"MAX({column}) AS hi{i}",
        ]
        if label is not None:
            metrics += [
                f"arg_min({identifier(label)}, {column}) AS at_lo{i}",
                f"arg_max({identifier(label)}, {column}) AS at_hi{i}",
            ]
    row = db.query(f"SELECT {', '.join(metrics)} FROM {identifier(table)}")[0]
    lines = [f"- строк в результате: {row['n']} (показано модели: {min(row['n'], shown)})"]
    for i, name in enumerate(numeric):
        low, high = f"мин {row[f'lo{i}']}", f"макс {row[f'hi{i}']}"
        if label is not None and row["n"] > 1:
            low += f" ({label} = {format_value(row[f'at_lo{i}'])})"
            high += f" ({label} = {format_value(row[f'at_hi{i}'])})"
        lines.append(
            f"- {name}: сумма {row[f's{i}']}, среднее {_round(row[f'a{i}'])}, {low}, {high}"
        )
    return "\n".join(lines)


# Номер периода года в выражении шага: EXTRACT(WEEK FROM d), date_part('month', d), week(d).
_PERIOD_OF_YEAR = re.compile(
    r"extract\s*\(\s*(week|isoweek|month|quarter|doy|dayofyear)\s+from"
    r"|date_part\s*\(\s*'(week|isoweek|month|quarter|doy|dayofyear)'"
    r"|\b(week|weekofyear|isoweek|month|quarter|dayofyear)\s*\(",
    re.IGNORECASE,
)
_PERIOD_UNITS = {
    "week": ("недели", "неделя"),
    "isoweek": ("недели", "неделя"),
    "weekofyear": ("недели", "неделя"),
    "month": ("месяца", "месяц"),
    "quarter": ("квартала", "квартал"),
    "doy": ("дня", "день"),
    "dayofyear": ("дня", "день"),
}


def period_number_facts(steps: list[ResearchStep]) -> list[str]:
    """Шаги, которые берут номер недели/месяца года: строка результата — все годы вместе.

    «Неделя 4» с SUM — сумма 4-х недель 2025 и 2026 годов, а MIN/MAX даты группы
    растягиваются на год: без факта модель называет её конкретной неделей.
    """
    lines = []
    for number, step in enumerate(steps, 1):
        for text in _strings([step.arguments, step.columns]):
            found = _PERIOD_OF_YEAR.search(text)
            if found is None:
                continue
            unit, noun = _PERIOD_UNITS[next(group for group in found.groups() if group).lower()]
            lines.append(
                f"- шаг {number} берёт номер {unit} года: строка результата объединяет этот "
                f"номер всех лет данных — это не конкретная {noun} с датами одного года, а итог "
                f"за все годы вместе; так и скажи, даты одного периода не называй"
            )
            break
    return lines


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def format_value(value: object) -> str:
    """Значение для промпта: даты в ISO, полночь без времени, дробные — до сотых."""
    if isinstance(value, datetime):
        return (
            value.date().isoformat()
            if value.time() == datetime.min.time()
            else value.isoformat(" ")
        )
    if isinstance(value, date):
        return value.isoformat()
    return str(_round(value))


def format_rows(records: list[dict]) -> str:
    """Строки результата компактной таблицей «a | b» вместо repr Python-объектов."""
    if not records:
        return "пусто"
    header = " | ".join(records[0])
    rows = [" | ".join(format_value(value) for value in record.values()) for record in records]
    return "\n".join([header, *rows])


def _round(value):
    return round(value, 2) if isinstance(value, float) else value
