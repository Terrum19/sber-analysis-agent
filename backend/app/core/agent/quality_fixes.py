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
from datetime import date, datetime, timedelta

from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.profiling import TableProfile
from app.core.llm.prompts.quality_rules import NULLS_HINT, PARTIAL_HINT, TIE_HINT
from app.core.tools.base import identifier
from app.core.tools.registry import tool_registry
from app.models.research_step import ResearchStep

# Число: «12345», «12 345» (разряды через пробел), «12,5», «12.5».
_NUMBER = re.compile(r"\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")
_SCALES = {"тыс": 10**3, "млн": 10**6, "млрд": 10**9}


def ungrounded_numbers(headline: str, sources: str) -> list[str]:
    """H-Q3: числа заголовка, которых нет в sources (текст промпта инсайта).

    Округление допускается до точности, с которой число написано в заголовке
    («12.3» опирается на 12.34). Проценты не проверяются: доли модель считает
    сама, в фактах их нет. Число со словом «тыс», «млн» или «млрд» сверяется
    и с опорными, делёнными на масштаб («726,9 млн» — 726930548.86), и с
    самими опорными (выручка уже в миллионах).
    """
    known = [_value(m) for m in _NUMBER.findall(sources)]
    missing = []
    for match in _NUMBER.finditer(headline):
        rest = headline[match.end() :].lstrip()
        if rest.startswith("%"):
            continue
        scale = next((size for word, size in _SCALES.items() if rest.startswith(word)), 1)
        text = match.group()
        value, tolerance = _value(text), _tolerance(text)
        if not any(
            abs(value - other / divisor) <= tolerance for other in known for divisor in {1, scale}
        ):
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


# Номер периода сезонности — подпись строки, хотя и число.
_PERIOD_NUMBER = re.compile(r"month|quarter|dayofweek|hour")

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


# NULL в строках и фактах для модели: «None» она приписывала соседней строке.
NULL_VALUE = "без значения"


def insight_facts(db: DuckDBManager, table: str, shown: int) -> str:
    """Число строк и итоги числовых колонок результата — посчитанные SQL, а не LLM.

    У минимума и максимума подписана строка, где они достигаются (первая
    нечисловая колонка: период, регион, id; у сезонности — номер месяца):
    «лучший месяц» читается из фактов, а не поиском по строкам. Колонка с
    одним значением во всех строках (p_value тренда) — само значение. Строка
    без подписи (NULL — например, продажи без магазина) подписана «без
    значения»: arg_max пропускает её и отдал бы подпись другой строки к
    чужому максимуму.
    """
    columns = db.query(f"DESCRIBE {identifier(table)}")
    numeric = [
        col["column_name"]
        for col in columns
        if str(col["column_type"]).upper().split("(")[0] in _NUMERIC
    ]
    label = next((c["column_name"] for c in columns if c["column_name"] not in numeric), None)
    if label is None and len(numeric) > 1 and _PERIOD_NUMBER.fullmatch(numeric[0]):
        label = numeric.pop(0)
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
            named = f"FILTER (WHERE {identifier(label)} IS NOT NULL)"
            metrics += [
                f"arg_min_null({identifier(label)}, {column}) AS at_lo{i}",
                f"arg_max_null({identifier(label)}, {column}) AS at_hi{i}",
                # arg_min/arg_max пропускают NULL в подписи — это лучшие подписанные строки.
                f"MIN({column}) {named} AS named_lo{i}",
                f"MAX({column}) {named} AS named_hi{i}",
                f"arg_min({identifier(label)}, {column}) AS named_at_lo{i}",
                f"arg_max({identifier(label)}, {column}) AS named_at_hi{i}",
            ]
    row = db.query(f"SELECT {', '.join(metrics)} FROM {identifier(table)}")[0]
    lines = [f"- строк в результате: {row['n']} (показано модели: {min(row['n'], shown)})"]
    for i, name in enumerate(numeric):
        if row["n"] > 1 and row[f"lo{i}"] is not None and row[f"lo{i}"] == row[f"hi{i}"]:
            lines.append(f"- {name}: {float(row[f'lo{i}']):.4g} (одно значение во всех строках)")
            continue
        low, high = f"мин {row[f'lo{i}']}", f"макс {row[f'hi{i}']}"
        if label is not None and row["n"] > 1:
            low += _extreme_label(row, label, "lo", i, "мин")
            high += _extreme_label(row, label, "hi", i, "макс")
        lines.append(
            f"- {name}: сумма {row[f's{i}']}, среднее {_round(row[f'a{i}'])}, {low}, {high}"
        )
    return "\n".join(lines)


def plan_columns(steps: list[ResearchStep], columns: list[str]) -> list[str]:
    """Колонки источника, которые использует план: факты о пропусках и датах — только по ним.

    Колонка использована, если её имя — значение аргумента («by»: ["country"])
    или отдельное слово в выражении («price > 100» — price, но не price_eur);
    регистр не важен, как в DuckDB. Колонок, созданных по ходу плана
    (is_expensive), в источнике нет — они отсеиваются сами.
    """
    texts = [text for step in steps for text in _strings([step.arguments, step.columns])]
    return [
        name
        for name in columns
        if any(re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE) for text in texts)
    ]


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def plan_facts(
    db: DuckDBManager,
    steps: list[ResearchStep],
    inputs: dict[int, str],
    profile: TableProfile | None,
    result_columns: list[str],
) -> tuple[list[str], list[str]]:
    """Факты о данных за результатом и подсказки инсайту к сработавшим фактам.

    Пропуски и период — из паспорта по колонкам плана: запросов к источнику
    нет, без паспорта этих фактов нет (профилировать посреди хода — полный
    скан внешней базы). Ничья — один запрос к входу шага sort_limit.
    """
    used = []
    if profile is not None:
        names = plan_columns(steps, [column.name for column in profile.columns])
        used = [profile.column(name) for name in names]
    nulls = [
        f"- в исходной таблице {c.name}: {c.nulls} из {c.total} без значения"
        for c in used
        if c.nulls
    ]
    periods = [
        (
            f"- в исходной таблице данные за период: {format_value(c.min_date)} – "
            f"{format_value(c.max_date)} ({c.name})"
        )
        for c in used
        if c.min_date is not None and c.max_date is not None
    ]
    partial = partial_periods(steps, profile) if "period" in result_columns else []
    found = [
        (nulls, NULLS_HINT),
        (periods, ""),
        (top_tie(db, steps, inputs), TIE_HINT),
        (partial, PARTIAL_HINT),
    ]
    return [line for lines, _ in found for line in lines], [h for lines, h in found if lines and h]


def top_tie(db: DuckDBManager, steps: list[ResearchStep], inputs: dict[int, str]) -> list[str]:
    """Ничья на вершине sort_limit: сколько строк его входа делят лучшее значение.

    Смотрится вход шага, а не результат: при limit=1 второе вино с тем же
    значением отсечено. Лучшее — по первой колонке order_by, остальные
    задают порядок внутри ничьей.
    """
    step = next(
        (s for s in reversed(steps) if s.tool == "sort_limit" and s.step_id in inputs), None
    )
    if step is None:
        return []
    args = tool_registry.get("sort_limit").parse_args(step.columns, step.arguments)
    order = [args.order_by] if isinstance(args.order_by, str) else args.order_by
    if not order:
        return []
    column, table = identifier(order[0]), identifier(inputs[step.step_id])
    best = "MAX" if args.descending else "MIN"
    row = db.query(
        f"SELECT COUNT(*) AS n, ANY_VALUE({column}) AS value FROM {table} "
        f"WHERE {column} = (SELECT {best}({column}) FROM {table})"
    )[0]
    if row["n"] < 2:
        return []
    value = format_value(row["value"])
    return [f"- ничья на вершине по {order[0]}: значение {value} у {row['n']} строк"]


def partial_periods(steps: list[ResearchStep], profile: TableProfile | None) -> list[str]:
    """Крайние периоды time_bucket, которые данные источника покрывают не целиком.

    Последний неполный, если данные кончаются раньше его последнего дня,
    первый — если начинаются позже его первого дня. Даты — из паспорта;
    колонки, созданной по ходу плана, в нём нет — проверки нет.
    """
    step = next((s for s in reversed(steps) if s.tool == "time_bucket"), None)
    if step is None or profile is None:
        return []
    args = tool_registry.get("time_bucket").parse_args(step.columns, step.arguments)
    name = args.date_column.lower()
    column = next((c for c in profile.columns if c.name.lower() == name), None)
    if (
        args.interval == "day"
        or column is None
        or not isinstance(column.min_date, date)
        or not isinstance(column.max_date, date)
    ):
        return []
    first, last = _day(column.min_date), _day(column.max_date)
    lines = []
    start = _period_start(first, args.interval)
    if first > start:
        lines.append(f"- период {start} неполный (данные с {first})")
    start = _period_start(last, args.interval)
    if last < _period_end(start, args.interval):
        lines.append(f"- период {start} неполный (данные по {last})")
    return lines


def _day(value: date) -> date:
    return value.date() if isinstance(value, datetime) else value


def _period_start(day: date, interval: str) -> date:
    """Начало периода, как date_trunc у time_bucket: неделя — с понедельника."""
    if interval == "week":
        return day - timedelta(days=day.weekday())
    month = {"month": day.month, "quarter": (day.month - 1) // 3 * 3 + 1, "year": 1}[interval]
    return date(day.year, month, 1)


def _period_end(start: date, interval: str) -> date:
    if interval == "week":
        return start + timedelta(days=6)
    years, month = divmod(start.month - 1 + {"month": 1, "quarter": 3, "year": 12}[interval], 12)
    return date(start.year + years, month + 1, 1) - timedelta(days=1)


def _extreme_label(row: dict, label: str, side: str, i: int, word: str) -> str:
    """Подпись экстремума; у строки без подписи — ещё и лучшая подписанная строка.

    Иначе при максимуме у NULL (продажи без магазина) лучший магазин модель
    угадывает по первым показанным строкам.
    """
    text = f" ({label} = {format_value(row[f'at_{side}{i}'])})"
    if row[f"at_{side}{i}"] is None and row[f"named_at_{side}{i}"] is not None:
        named = format_value(row[f"named_at_{side}{i}"])
        text += f"; среди строк с {label} {word} {row[f'named_{side}{i}']} ({label} = {named})"
    return text


def format_value(value: object) -> str:
    """Значение для промпта: даты в ISO, полночь без времени, дробные — до сотых.

    Дробные меньше 1 по модулю — 3 значащие цифры: доля 0.0259 не становится
    «0.03», p-value 1e-30 — «0.0».
    """
    if value is None:
        return NULL_VALUE
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
    if not isinstance(value, float):
        return value
    return float(f"{value:.3g}") if abs(value) < 1 else round(value, 2)
