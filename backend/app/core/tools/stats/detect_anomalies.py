from typing import Literal

from pydantic import Field, model_validator

from app.core.tools.base import BaseTool, Ident, ToolArgs, identifier


class DetectAnomaliesArgs(ToolArgs):
    value: Ident
    by: Ident | None = None
    period: Ident | None = None
    method: Literal["iqr", "zscore"] = "iqr"
    threshold: float = Field(default=1.5, gt=0)

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: dict) -> dict:
        if isinstance(data, dict) and isinstance(data.get("method"), str):
            data["method"] = data["method"].lower()
        return data


class DetectAnomaliesTool(BaseTool):
    name = "detect_anomalies"
    description = (
        "Аномалии и выбросы: строки, где значение метрики выбивается из своей группы "
        "(аномальные, нетипичные, подозрительные значения); method=iqr — за пределами "
        "Q1 - threshold*IQR..Q3 + threshold*IQR (threshold 1.5), zscore — "
        "|значение - среднее| / стандартное отклонение больше threshold (обычно 3); "
        "by — считать границы отдельно в каждой группе; period — сначала сложить value "
        "по period (и by) и искать аномальные периоды; результат — только выбросы с "
        "колонками score (во сколько IQR или сигм за границей) и direction (high/low). "
        "Пример: «Есть ли аномальные значения возраста?», «В какие дни выручка была "
        "аномальной?»"
    )
    kind = "analysis"
    Args = DetectAnomaliesArgs

    def execute(self, db, input_table: str, args: DetectAnomaliesArgs) -> str:
        source = _source(input_table, args)
        partition = f"PARTITION BY {identifier(args.by)}" if args.by else ""
        measure = identifier("value") if args.period else identifier(args.value)
        score, direction = _score(args.method, measure)
        order = f"{identifier(args.period)}, " if args.period else ""
        sql = (
            f"SELECT * EXCLUDE (_center, _spread, _lower, _upper), {score} AS score, "
            f"{direction} AS direction FROM ("
            f"SELECT *, {_window_stats(args.method, measure, partition)} FROM ({source})) "
            f"WHERE {score} > {float(args.threshold)!r} "
            f"ORDER BY {order}score DESC"
        )
        return db.save_step_result(input_table, sql)


def _source(input_table: str, args: DetectAnomaliesArgs) -> str:
    value = identifier(args.value)
    table = identifier(input_table)
    if not args.period:
        return f"SELECT * FROM {table} WHERE {value} IS NOT NULL"
    keys = [identifier(args.by)] if args.by else []
    keys.append(identifier(args.period))
    key_list = ", ".join(keys)
    return (
        f"SELECT {key_list}, SUM({value}) AS {identifier('value')} FROM {table} "
        f"WHERE {value} IS NOT NULL AND {identifier(args.period)} IS NOT NULL "
        f"GROUP BY {key_list}"
    )


def _window_stats(method: str, measure: str, partition: str) -> str:
    if method == "zscore":
        center = f"AVG({measure}) OVER ({partition})"
        spread = f"STDDEV_SAMP({measure}) OVER ({partition})"
        return f"{center} AS _center, {spread} AS _spread, {center} AS _lower, {center} AS _upper"
    lower = f"quantile_cont({measure}, 0.25) OVER ({partition})"
    upper = f"quantile_cont({measure}, 0.75) OVER ({partition})"
    fallback = f"STDDEV_POP({measure}) OVER ({partition})"
    spread = f"COALESCE(NULLIF({upper} - {lower}, 0), {fallback})"
    return f"0 AS _center, {spread} AS _spread, {lower} AS _lower, {upper} AS _upper"


def _score(method: str, measure: str) -> tuple[str, str]:
    direction = f"CASE WHEN {measure} > _upper THEN 'high' ELSE 'low' END"
    if method == "zscore":
        return f"ABS({measure} - _center) / NULLIF(_spread, 0)", direction
    excess = (
        f"CASE WHEN {measure} > _upper THEN {measure} - _upper "
        f"WHEN {measure} < _lower THEN _lower - {measure} ELSE 0 END"
    )
    return f"({excess}) / NULLIF(_spread, 0)", direction
