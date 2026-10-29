from typing import Literal

from pydantic import Field, model_validator

from app.core.tools.base import BaseTool, Ident, ToolArgs, identifier

_STEP_FUNCTIONS = {"day": "to_days", "week": "to_weeks", "month": "to_months"}


class LagCorrelationArgs(ToolArgs):
    date: Ident
    x: Ident
    y: Ident
    max_lag: int = Field(default=12, ge=0, le=366)
    interval: Literal["day", "week", "month"] = "month"

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: dict) -> dict:
        if isinstance(data, dict) and isinstance(data.get("interval"), str):
            data["interval"] = data["interval"].lower()
        return data


class LagCorrelationTool(BaseTool):
    name = "lag_correlation"
    description = (
        "Лаговая корреляция: запаздывание, задержка, опережение одной метрики другой; "
        "x и y суммируются по периодам interval (day/week/month), для каждого lag от 0 "
        "до max_lag считается corr(x в период t, y в период t - lag); максимум corr при "
        "lag > 0 значит, что y опережает x на lag периодов; результат — lag, corr, n "
        "(число пар периодов). Пример: «Через сколько месяцев расходы на рекламу "
        "отражаются на выручке?», «Есть ли запаздывание между обращениями и оттоком?»"
    )
    kind = "analysis"
    Args = LagCorrelationArgs

    def execute(self, db, input_table: str, args: LagCorrelationArgs) -> str:
        date_column = identifier(args.date)
        step = _STEP_FUNCTIONS[args.interval]
        series = (
            f"SELECT date_trunc('{args.interval}', {date_column}) AS period, "
            f"SUM({identifier(args.x)}) AS x_value, SUM({identifier(args.y)}) AS y_value "
            f"FROM {identifier(input_table)} WHERE {date_column} IS NOT NULL GROUP BY 1"
        )
        sql = (
            f"WITH series AS ({series}), "
            f"lags AS (SELECT CAST(range AS INTEGER) AS lag FROM range(0, {args.max_lag + 1})) "
            "SELECT lags.lag, corr(current.x_value, earlier.y_value) AS corr, "
            "COUNT(earlier.y_value) AS n "
            "FROM lags CROSS JOIN series AS current "
            "LEFT JOIN series AS earlier "
            f"ON earlier.period = current.period - {step}(lags.lag) "
            "AND current.x_value IS NOT NULL AND earlier.y_value IS NOT NULL "
            "GROUP BY lags.lag ORDER BY lags.lag"
        )
        return db.save_step_result(input_table, sql)
