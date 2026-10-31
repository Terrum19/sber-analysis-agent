"""Проверки результата шага без LLM (A-29, режим «Проверка»).

Шаг выполнился без ошибки, но результат подозрителен: пусто, фильтр оставил
почти ничего, join раздул строки, вопрос про ряд, а строка одна. Такие
аномалии (REPAIRABLE) оркестратор отдаёт в fix_step, как текст ошибки.
Остальные находки (пропуски, неполный период, константа) исправлением шага
не лечатся: они идут в улики критика.
"""

import logging
import re

import duckdb

from app.core.agent.step_executor import tool_kind
from app.core.tools.base import identifier
from app.core.tools.registry import tool_registry
from app.models.research_step import ResearchStep
from app.models.review import StepFinding

logger = logging.getLogger(__name__)

# Фильтр, оставивший меньше этой доли входа (на входе не меньше FILTER_MIN_ROWS строк).
FILTER_COLLAPSE_SHARE = 0.01
FILTER_MIN_ROWS = 100
# Доля NULL в колонке, которую использует шаг.
NULL_SHARE = 0.3
# Крайний период time_bucket, покрытый данными меньше чем на эту долю, — неполный.
PERIOD_COVERAGE = 0.9
# Константная метрика подозрительна от стольких строк.
CONSTANT_MIN_ROWS = 3
# Служебные счётчики seasonality (число периодов, неполные, пропущенные): одно
# значение на всех строках — норма, а не схлопнутая метрика.
SERVICE_COLUMNS = frozenset({"periods", "incomplete", "skipped"})

FILTER_TOOLS = ("filter_rows",)
JOIN_TOOLS = ("join_tables", "left_join_tables", "join_related")
# Неполный период: длина периода time_bucket (часы и дни не проверяем — там это норма).
PERIODS = {"week": "7 days", "month": "1 month", "quarter": "3 months", "year": "1 year"}
# Вопрос просит ряд или разрез, а не одно число.
SERIES = re.compile(
    r"по\s+(дням|дня|недел|месяц|кварт|годам|года|регион|город|категори|канал|сегмент|"
    r"товар|продукт|клиент|менеджер|каждо)|динамик|тренд|помесячн|в разрезе|топ|рейтинг",
    re.IGNORECASE,
)

# Аномалии, которые исправляет fix_step; остальные — улики критика.
REPAIRABLE = ("empty", "filter_collapse", "join_blowup", "single_row")
FIX_HINT = (
    "Шаг выполнился, но результат подозрителен: {problems}. Исправь аргументы шага "
    "(значение и оператор фильтра, регистр и формат значений, ключи объединения, "
    "разрез группировки). Если шаг верен — верни его без изменений."
)


def inspect_step(
    db, step: ResearchStep, input_table: str, table: str, question: str
) -> list[StepFinding]:
    """Аномалии результата шага; таблица не читается — находок нет, ход идёт дальше."""
    try:
        return _inspect(db, step, input_table, table, question)
    except duckdb.Error:
        logger.warning("проверка шага %d не выполнена", step.step_id, exc_info=True)
        return []


def repairable(findings: list[StepFinding]) -> list[StepFinding]:
    return [finding for finding in findings if finding.code in REPAIRABLE]


def fix_feedback(findings: list[StepFinding]) -> str:
    """Текст для fix_step вместо ошибки выполнения."""
    return FIX_HINT.format(problems="; ".join(finding.message for finding in findings))


def _inspect(
    db, step: ResearchStep, input_table: str, table: str, question: str
) -> list[StepFinding]:
    rows_out = _count(db, table)
    rows_in = _count(db, input_table)
    findings: list[StepFinding] = []
    if rows_out == 0:
        findings.append(
            StepFinding(code="empty", message=f"результат пуст (на входе {rows_in} строк)")
        )
    if (
        step.tool in FILTER_TOOLS
        and rows_in >= FILTER_MIN_ROWS
        and 0 < rows_out < rows_in * FILTER_COLLAPSE_SHARE
    ):
        findings.append(
            StepFinding(
                code="filter_collapse",
                message=f"фильтр оставил {rows_out} строк из {rows_in}",
            )
        )
    if step.tool in JOIN_TOOLS and rows_out > rows_in > 0:
        findings.append(
            StepFinding(
                code="join_blowup",
                message=(
                    f"после объединения строк стало больше: {rows_out} против {rows_in} — "
                    "ключ второй таблицы не уникален"
                ),
            )
        )
    if rows_out == 1 and rows_in > 1 and tool_kind(step) == "transform" and SERIES.search(question):
        findings.append(
            StepFinding(
                code="single_row",
                message="вопрос про ряд или разрез, а в результате одна строка",
            )
        )
    columns = dict(_types(db, input_table))
    for column in used_columns(step, columns):
        share = _null_share(db, input_table, column)
        if share >= NULL_SHARE:
            findings.append(
                StepFinding(code="nulls", message=f"в колонке {column} пропусков {share:.0%}")
            )
    if step.tool == "time_bucket" and rows_out > 0:
        findings += _partial_periods(db, step, input_table, table, columns)
    if rows_out >= CONSTANT_MIN_ROWS:
        findings += _constants(db, table)
    return findings


def used_columns(step: ResearchStep, columns: dict[str, str] | list[str]) -> list[str]:
    """Колонки входа, которые шаг называет в columns или аргументах."""
    names = [*step.columns, *_strings(step.arguments)]
    return [name for name in dict.fromkeys(names) if name in columns]


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def _count(db, table: str) -> int:
    return int(db.query(f"SELECT count(*) AS n FROM {identifier(table)}")[0]["n"])


def _types(db, table: str) -> list[tuple[str, str]]:
    rows = db.query(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = $tbl ORDER BY ordinal_position",
        {"tbl": table},
    )
    return [(row["column_name"], row["data_type"]) for row in rows]


def _null_share(db, table: str, column: str) -> float:
    row = db.query(
        f"SELECT count(*) AS n, count({identifier(column)}) AS filled FROM {identifier(table)}"
    )[0]
    return 0.0 if not row["n"] else 1 - row["filled"] / row["n"]


def _partial_periods(
    db, step: ResearchStep, input_table: str, table: str, columns: dict[str, str]
) -> list[StepFinding]:
    """Первый и последний период time_bucket, покрытые данными не целиком."""
    try:
        args = tool_registry.get("time_bucket").parse_args(step.columns, step.arguments)
    except ValueError:
        return []
    interval = PERIODS.get(args.interval)
    column = args.date_column
    if interval is None or column not in columns:
        return []
    # Дата без времени покрывает весь свой день.
    day = " + INTERVAL 1 DAY" if columns[column].upper() == "DATE" else ""
    value = f"TRY_CAST({identifier(column)} AS TIMESTAMP)"
    row = db.query(
        f"WITH d AS (SELECT min({value}) AS lo, max({value}){day} AS hi "
        f"FROM {identifier(input_table)}), "
        f"p AS (SELECT min(CAST(period AS TIMESTAMP)) AS first, "
        f"max(CAST(period AS TIMESTAMP)) AS last FROM {identifier(table)}) "
        f"SELECT epoch(first + INTERVAL '{interval}') - epoch(lo) AS head, "
        f"epoch(first + INTERVAL '{interval}') - epoch(first) AS head_len, "
        f"epoch(hi) - epoch(last) AS tail, "
        f"epoch(last + INTERVAL '{interval}') - epoch(last) AS tail_len, "
        f"strftime(first, '%Y-%m-%d') AS first, strftime(last, '%Y-%m-%d') AS last, "
        f"strftime(lo, '%Y-%m-%d') AS lo, strftime(hi{' - INTERVAL 1 DAY' if day else ''}, "
        f"'%Y-%m-%d') AS hi FROM d, p"
    )[0]
    if row["head_len"] is None or row["tail_len"] is None:
        return []
    findings = []
    if row["head"] < row["head_len"] * PERIOD_COVERAGE:
        findings.append(
            StepFinding(
                code="partial_period",
                message=f"первый период {row['first']} неполный: данные с {row['lo']}",
            )
        )
    if row["last"] != row["first"] and row["tail"] < row["tail_len"] * PERIOD_COVERAGE:
        findings.append(
            StepFinding(
                code="partial_period",
                message=f"последний период {row['last']} неполный: данные по {row['hi']}",
            )
        )
    return findings


def _constants(db, table: str) -> list[StepFinding]:
    """Числовая колонка результата с одним значением на всех строках."""
    numeric = [
        name
        for name, kind in _types(db, table)
        if name not in SERVICE_COLUMNS
        and any(word in kind.upper() for word in ("INT", "DOUBLE", "DECIMAL", "FLOAT", "REAL"))
    ]
    if not numeric:
        return []
    distinct = ", ".join(
        f"count(DISTINCT {identifier(name)}) AS {identifier(name)}" for name in numeric
    )
    row = db.query(f"SELECT {distinct} FROM {identifier(table)}")[0]
    return [
        StepFinding(code="constant", message=f"в колонке {name} одно значение на всех строках")
        for name in numeric
        if row[name] == 1
    ]
