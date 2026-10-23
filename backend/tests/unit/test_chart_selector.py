import datetime as dt
from decimal import Decimal

import pytest

from app.core.viz.chart_selector import is_single_number, select_chart


def test_explicit_request_wins() -> None:
    assert select_chart("распределение сумм", "t", "scatter") == "scatter"
    assert select_chart("сколько всего", "t", "bar", records=[{"total": 5}]) == "bar"


@pytest.mark.parametrize(
    ("records", "expected"),
    [
        ([{"total": 5}], True),
        ([{"total": Decimal("1.5")}], True),
        ([{"total": 2.5}], True),
        ([{"total": None}], False),
        ([{"total": float("nan")}], False),
        ([{"total": float("inf")}], False),
        ([{"total": Decimal("NaN")}], False),
        ([{"total": True}], False),
        ([{"total": "5"}], False),
        ([{"day": dt.date(2024, 1, 1)}], False),
        ([{"total": 5, "orders": 3}], False),
        ([{"total": 5}, {"total": 6}], False),
        ([], False),
        (None, False),
    ],
)
def test_single_number(records: list[dict] | None, expected: bool) -> None:
    assert is_single_number(records) is expected


def test_single_number_result_becomes_kpi() -> None:
    assert select_chart("Какая выручка за квартал?", "t", records=[{"total": 10}]) == "kpi"
    assert select_chart("Динамика выручки", "t", records=[{"total": 10}, {"total": 11}]) == "line"


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Распределение чеков", "histogram"),
        ("Покажи гистограмму", "histogram"),
        ("Динамика продаж", "line"),
        ("Выручка по месяцам", "line"),
        ("Сравни регионы", "bar"),
        ("Есть ли связь цены и спроса", "scatter"),
        ("Топ товаров", "bar"),
    ],
)
def test_keyword_heuristics(question: str, expected: str) -> None:
    assert select_chart(question, "t") == expected
