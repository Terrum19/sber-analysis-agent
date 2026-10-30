"""TS-тул: сезонность (типичный уровень по календарному периоду).

Пример сценария: «Какой паттерн повторяется каждые 12 месяцев?»,
«В какой день недели больше заказов?».
"""

from datetime import timedelta
from statistics import median
from typing import Literal

from pydantic import model_validator

from app.core.tools.base import BaseTool, Ident, MetricArgs
from app.core.tools.domain._common import _MONDAY, rows_result, series

NAME = "seasonality"

_BUCKETS = {
    "month": "month",
    "quarter": "quarter",
    "week": "week",
    "dayofweek": "day",
    "hour": "hour",
}
# Неделя года — 52 шага: 53-я неделя бывает не каждый год, у неё меньше periods.
_CYCLES = {"month": 12, "quarter": 4, "week": 52, "dayofweek": 7, "hour": 24}
# Шагов в окне, где пустой период — ноль, а не дыра: час — в своём дне, день — в неделе.
_ZERO_WINDOWS = {"hour": 24, "dayofweek": 7}


class SeasonalityArgs(MetricArgs):
    date_column: Ident
    metric_column: Ident | None = None
    period: Literal["month", "quarter", "week", "dayofweek", "hour"] = "month"
    function: Literal["SUM", "COUNT", "AVG"] = "SUM"

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: dict) -> dict:
        if isinstance(data, dict) and isinstance(data.get("function"), str):
            data = {**data, "function": data["function"].upper()}
        return data

    @model_validator(mode="after")
    def _metric_for_function(self) -> "SeasonalityArgs":
        if self.metric_column is None and self.function != "COUNT":
            raise ValueError(f"seasonality: {self.function} requires metric_column")
        return self


class _Series:
    """Аргументы series(): итог function за каждый конкретный период."""

    def __init__(self, args: SeasonalityArgs, value_column: str | None, function: str) -> None:
        self.date_column = args.date_column
        self.value_column = value_column
        self.interval = _BUCKETS[args.period]
        self.function = function
        self.by: list[str] = []


class SeasonalityTool(BaseTool):
    """Типичный уровень метрики по номеру периода, все годы вместе."""

    name = NAME
    description = (
        "Сезонность: типичный уровень метрики по month/quarter/week (номер недели года ISO)/"
        "dayofweek (1=пн)/hour, все годы вместе — «типичная неделя года», а не конкретная неделя; function=SUM — итог за конкретный период (выручка, штуки, объёмы), COUNT — "
        "число строк (событий, заказов), AVG — средняя величина одной записи (средний чек, "
        "цена, длительность); сначала итог за каждый месяц/день/час, потом среднее по годам "
        "(дням) — level; median — медиана тех же периодов (намного ниже level — выбросы); "
        "periods — сколько периодов усреднено: при SUM/COUNT час без записей в дне с данными "
        "и день без записей в неделе с данными — ноль, месяц и квартал без данных — пропуск; "
        "неполные крайние периоды "
        "(incomplete) и лишние сверх целых лет (skipped) в расчёт не входят"
    )
    Args = SeasonalityArgs

    def execute(self, db, input_table: str, args: SeasonalityArgs) -> str:
        totals = _totals(db, input_table, args)
        cycle = _CYCLES[args.period]
        complete = [step for step, (_, _, incomplete) in totals.items() if not incomplete]
        if not complete:
            raise ValueError("seasonality: нет ни одного полного периода")
        first, last = min(complete), max(complete)
        # Целые годы — по всему охвату, с неполными краями: у недель края почти всегда
        # неполные, и два года данных давали 103 полные недели — один «целый год».
        cycles = (max(totals) - min(totals) + 1) // cycle
        start = last - cycles * cycle + 1 if cycles else first
        levels: dict[int, list] = {}
        values: dict[int, list[float]] = {}
        zero_window = _ZERO_WINDOWS.get(args.period) if args.function != "AVG" else None
        active = {step // zero_window for step in totals} if zero_window else set()
        for step in range(first, last + 1):
            number = _number(step, args.period)
            level = levels.setdefault(number, [0.0, 0, 0, 0])
            if step in totals:
                total, count, _ = totals[step]
            elif zero_window and step // zero_window in active:
                # Ночной час без продаж — 0 ₽, а не «нет данных»: без нуля ночь завышена.
                total, count = 0.0, 0
            else:
                continue
            if step < start:
                level[3] += 1
                continue
            level[0] += total
            level[1] += count
            level[2] += 1
            if args.function != "AVG":
                values.setdefault(number, []).append(total)
            elif count:
                values.setdefault(number, []).append(total / count)
        for step, (_, _, incomplete) in totals.items():
            if incomplete:
                levels.setdefault(_number(step, args.period), [0.0, 0, 0, 0])
        rows = []
        for number in sorted(levels):
            total, count, periods, skipped = levels[number]
            incomplete = sum(
                1
                for step, (_, _, partial) in totals.items()
                if partial and _number(step, args.period) == number
            )
            if args.function == "AVG":
                level = total / count if count else None
            else:
                level = total / periods if periods else None
            middle = median(values[number]) if values.get(number) else None
            rows.append((number, level, middle, periods, incomplete, skipped))
        return rows_result(
            db,
            input_table,
            [args.period, "level", "median", "periods", "incomplete", "skipped"],
            rows,
        )


def _totals(db, table: str, args: SeasonalityArgs) -> dict:
    """{шаг: (итог, число записей, неполный)} по каждому конкретному периоду.

    Для AVG итог — сумма, а среднее считается по всем записям окна.
    """
    column, average = args.metric_column, args.function == "AVG"
    sums = series(db, table, _Series(args, column, "SUM" if average else args.function), NAME)[1]
    counts = {}
    if average:
        counted = series(db, table, _Series(args, column, "COUNT"), NAME)[1]
        counts = {point.step: point.value for point in counted[0][1]}
    return {
        point.step: (point.value, int(counts.get(point.step, 1)), point.incomplete)
        for point in sums[0][1]
    }


def _number(step: int, period: str) -> int:
    """Номер периода по шагу: месяц 1-12, квартал 1-4, неделя ISO 1-53, день недели 1=пн, час 0-23."""
    if period == "week":
        return (_MONDAY + timedelta(weeks=step)).isocalendar().week
    if period == "hour":
        return step % 24
    if period == "dayofweek":
        return step % 7 + 1
    return step % _CYCLES[period] + 1
