"""Улики для критика (A-29): что посчитано, на каких данных и что вышло.

Критик не смотрит в данные сам: «проверь выбросы» без чисел даёт либо
«всё хорошо», либо выдуманную претензию. Поэтому код считает по исходной
таблице и шагам плана то, о чём спрашивает чек-лист: строки на входе и
выходе шагов, пропуски, выбросы и диапазон дат использованных колонок,
колонки, которых план не коснулся. И знаки с категориями: у возвратов
количество отрицательное, а «Тип операции» = «Возврат»; без этого критик
не видел, что возвраты уже вычтены или, наоборот, прибавлены (eval 31.10).
"""

import logging

import duckdb

from app.core.agent.assurance.checks import used_columns
from app.core.agent.quality_fixes import format_rows, insight_facts
from app.core.tools.base import identifier
from app.models.insight import Insight
from app.models.research_step import ResearchStep
from app.models.review import StepFinding

logger = logging.getLogger(__name__)

RESULT_ROWS = 20
SUPPORTING_ROWS = 10
# Неиспользованных колонок в уликах не больше стольких.
UNUSED_COLUMNS = 40
# Текстовая колонка с не большим числом разных значений — категория: значения в уликах.
CATEGORY_VALUES = 10
CATEGORY_COLUMNS = 20

NUMERIC = ("INT", "DOUBLE", "DECIMAL", "FLOAT", "REAL", "NUMERIC")
TEMPORAL = ("DATE", "TIMESTAMP")


def build_evidence(
    db,
    *,
    question: str,
    steps: list[ResearchStep],
    tables: dict[int, str],
    inputs: dict[int, str],
    result_table: str,
    supporting: list[tuple[str, str]],
    findings: dict[int, list[StepFinding]],
    insight: Insight,
    source: str,
    titles: dict[str, str] | None = None,
    memories: list[str] | None = None,
) -> str:
    """Текст улик; то, что не посчиталось, пропускается — проверка идёт дальше."""
    titles = titles or {}
    blocks = [f"Вопрос пользователя: {question}"]
    if memories:
        blocks.append("Воспоминания сессии:\n" + "\n".join(f"- {text}" for text in memories))
    blocks.append("Шаги плана:\n" + _steps(db, steps, tables, inputs, findings))
    try:
        source_columns = _types(db, source)
    except duckdb.Error:
        source_columns = []
    used = [
        name
        for name in dict.fromkeys(
            name for step in steps for name in used_columns(step, dict(source_columns))
        )
    ]
    stats = [line for name in used if (line := _column(db, source, name, dict(source_columns)))]
    if stats:
        blocks.append(f"Использованные колонки исходной таблицы {source}:\n" + "\n".join(stats))
    dates = [
        line
        for name, kind in source_columns
        if name not in used and kind.upper().startswith(TEMPORAL)
        if (line := _column(db, source, name, dict(source_columns)))
    ]
    categories = [
        line
        for name, kind in source_columns
        if kind.upper() == "VARCHAR"
        if (line := _categories(db, source, name))
    ][:CATEGORY_COLUMNS]
    if categories:
        blocks.append(
            f"Значения категориальных колонок (до {CATEGORY_VALUES} вариантов):\n"
            + "\n".join(categories)
        )
    if dates:
        blocks.append("Даты исходной таблицы, которых план не касался:\n" + "\n".join(dates))
    unused = [
        f"- {name} ({kind})" + (f" — {titles[name]}" if titles.get(name, name) != name else "")
        for name, kind in source_columns
        if name not in used
    ][:UNUSED_COLUMNS]
    if unused:
        blocks.append(
            "Колонки исходной таблицы, которых план не использовал:\n" + "\n".join(unused)
        )
    blocks.append("Результат ответа:\n" + _rows(db, result_table, RESULT_ROWS))
    for purpose, table in supporting:
        blocks.append(f"Сопутствующий результат «{purpose}»:\n" + _rows(db, table, SUPPORTING_ROWS))
    try:
        facts = insight_facts(db, result_table, shown=RESULT_ROWS)
    except Exception:
        logger.warning("улики: факты по %s не посчитались", result_table, exc_info=True)
        facts = ""
    if facts:
        blocks.append("Факты по результату (посчитаны SQL):\n" + facts)
    answer = [f"Заголовок: {insight.headline}", *(f"- {line}" for line in insight.details)]
    if insight.limits:
        answer.append(f"Ограничения: {insight.limits}")
    blocks.append("Вывод, который получил пользователь:\n" + "\n".join(answer))
    return "\n\n".join(blocks)


def describe_findings(findings: dict[int, list[StepFinding]]) -> str:
    return "\n".join(
        f"- шаг {step_id}: {finding.message}"
        for step_id, items in sorted(findings.items())
        for finding in items
    )


def _steps(
    db,
    steps: list[ResearchStep],
    tables: dict[int, str],
    inputs: dict[int, str],
    findings: dict[int, list[StepFinding]],
) -> str:
    lines = []
    for step in steps:
        if step.step_id not in tables:
            continue
        rows_in = _count(db, inputs.get(step.step_id))
        rows_out = _count(db, tables[step.step_id])
        arguments = step.arguments or {"columns": step.columns}
        line = (
            f"- шаг {step.step_id} {step.tool}({arguments}) — {step.purpose}; "
            f"строк на входе {rows_in}, на выходе {rows_out}"
        )
        found = findings.get(step.step_id) or []
        if found:
            line += "; аномалии: " + "; ".join(finding.message for finding in found)
        lines.append(line)
    return "\n".join(lines) or "нет"


def _count(db, table: str | None) -> str:
    if not table:
        return "?"
    try:
        return str(db.query(f"SELECT count(*) AS n FROM {identifier(table)}")[0]["n"])
    except duckdb.Error:
        return "?"


def _types(db, table: str) -> list[tuple[str, str]]:
    rows = db.query(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = $tbl ORDER BY ordinal_position",
        {"tbl": table},
    )
    return [(row["column_name"], row["data_type"]) for row in rows]


def _column(db, table: str, name: str, types: dict[str, str]) -> str:
    """Пропуски, а для чисел — распределение и выбросы по IQR, для дат — диапазон."""
    kind = types.get(name, "").upper()
    column, source = identifier(name), identifier(table)
    try:
        if any(word in kind for word in NUMERIC):
            row = db.query(
                f"WITH q AS (SELECT quantile_cont({column}, 0.25) AS q1, "
                f"quantile_cont({column}, 0.75) AS q3 FROM {source}) "
                f"SELECT count(*) AS n, count({column}) AS filled, min({column}) AS lo, "
                f"max({column}) AS hi, avg({column}) AS mean, median({column}) AS med, "
                f"count(*) FILTER (WHERE {column} < q1 - 1.5 * (q3 - q1) "
                f"OR {column} > q3 + 1.5 * (q3 - q1)) AS outliers, "
                f"count(*) FILTER (WHERE {column} < 0) AS negative FROM {source}, q"
            )[0]
            if not row["n"]:
                return f"- {name}: таблица пуста"
            return (
                f"- {name} ({kind}): пропусков {_share(row['n'] - row['filled'], row['n'])}, "
                f"min {_num(row['lo'])}, max {_num(row['hi'])}, среднее {_num(row['mean'])}, "
                f"медиана {_num(row['med'])}, выбросов по IQR {_share(row['outliers'], row['n'])}, "
                f"отрицательных {_share(row['negative'], row['n'])}"
            )
        if kind.startswith(TEMPORAL):
            row = db.query(
                f"SELECT count(*) AS n, count({column}) AS filled, "
                f"strftime(min({column}), '%Y-%m-%d') AS lo, "
                f"strftime(max({column}), '%Y-%m-%d') AS hi FROM {source}"
            )[0]
            return (
                f"- {name} ({kind}): данные с {row['lo']} по {row['hi']}, "
                f"пропусков {_share(row['n'] - row['filled'], row['n'])}"
            )
        row = db.query(
            f"SELECT count(*) AS n, count({column}) AS filled, "
            f"count(DISTINCT {column}) AS distinct_n FROM {source}"
        )[0]
        return (
            f"- {name} ({kind}): пропусков {_share(row['n'] - row['filled'], row['n'])}, "
            f"разных значений {row['distinct_n']}"
        )
    except duckdb.Error:
        logger.warning("улики: колонка %s.%s не посчиталась", table, name, exc_info=True)
        return ""


def _categories(db, table: str, name: str) -> str:
    """Все значения колонки с числом строк, если вариантов не больше CATEGORY_VALUES."""
    column = identifier(name)
    try:
        rows = db.query(
            f"SELECT {column} AS value, count(*) AS n FROM {identifier(table)} "
            f"GROUP BY 1 ORDER BY n DESC LIMIT {CATEGORY_VALUES + 1}"
        )
    except duckdb.Error:
        logger.warning("улики: значения %s.%s не прочитаны", table, name, exc_info=True)
        return ""
    if not rows or len(rows) > CATEGORY_VALUES:
        return ""
    values = ", ".join(f"{row['value']} — {row['n']}" for row in rows)
    return f"- {name}: {values}"


def _rows(db, table: str, limit: int) -> str:
    try:
        total = db.query(f"SELECT count(*) AS n FROM {identifier(table)}")[0]["n"]
        rows = db.to_records(table, limit=limit)
    except duckdb.Error:
        return "не прочитан"
    shown = format_rows(rows) if rows else "пусто"
    return f"строк всего {total}, первые {min(total, limit)}:\n{shown}"


def _share(part: int, total: int) -> str:
    return f"{part / total:.1%} ({part} из {total})" if total else "0"


def _num(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.4g}" if abs(value) < 1e6 else f"{value:,.0f}".replace(",", " ")
    return str(value)
