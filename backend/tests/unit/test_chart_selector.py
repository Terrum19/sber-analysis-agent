import datetime as dt
from decimal import Decimal

import pytest

from app.core.viz.chart_selector import fits, histogram_column, is_single_number, select_chart
from app.core.viz.vega_spec_builder import infer_field_types

MONTHS = [{"month": f"2024-{month:02d}", "revenue": month * 10} for month in range(1, 13)]
DAILY = [
    {"period": dt.date(2024, 1, 1) + dt.timedelta(days=offset), "consumption": 100 + offset}
    for offset in range(60)
]
REGIONS = [{"region": "msk", "revenue": 12.5}, {"region": "spb", "revenue": 4.0}]
SAMPLES = [{"amount": float(value)} for value in range(25)]
PAIRS = [{"price": value, "demand": 100 - value} for value in range(5)]
ORDERS = [{"order_id": value, "price": value % 7 * 10.0, "qty": value % 3} for value in range(25)]
MANY_CATEGORIES = [{"sku": f"sku-{value}", "sold": value} for value in range(60)]


def test_explicit_request_wins_without_records() -> None:
    assert select_chart("распределение сумм", "t", "scatter") == "scatter"


def test_planner_choice_is_checked_against_the_result() -> None:
    assert select_chart("сколько всего", "t", "bar", records=[{"total": 5}]) == "kpi"
    assert select_chart("итоги", "t", "line", records=[{"total": 5, "orders": 3}]) == "table"
    assert select_chart("сравни", "t", "line", records=REGIONS) == "table"
    assert select_chart("по месяцам", "t", "line", records=MONTHS) == "line"


def test_no_chart_is_kept() -> None:
    assert select_chart("сколько всего", "t", "none", records=[{"total": 5}]) == "none"


@pytest.mark.parametrize(
    ("chart_type", "records", "expected"),
    [
        ("kpi", [{"total": 5}], True),
        ("bar", [{"total": 5}], False),
        ("table", [{"mean": 1.5, "std": 0.2, "p_value": 0.03}], True),
        ("kpi", [{"mean": 1.5, "std": 0.2, "p_value": 0.03}], False),
        ("line", MONTHS[:2], False),
        ("line", MONTHS, True),
        ("area", MONTHS, True),
        ("line", REGIONS, False),
        ("scatter", PAIRS, True),
        ("scatter", MONTHS, False),
        ("histogram", SAMPLES, True),
        ("histogram", SAMPLES[:10], False),
        ("histogram", PAIRS, False),
        ("bar", REGIONS, True),
        ("bar", MANY_CATEGORIES, False),
        ("bar", PAIRS, False),
        ("heatmap", REGIONS, False),
        ("box", SAMPLES, True),
        ("box", [{"region": "msk"}, {"region": "spb"}], False),
    ],
)
def test_fits(chart_type: str, records: list[dict], expected: bool) -> None:
    assert fits(chart_type, records, infer_field_types(records)) is expected


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
    assert select_chart("Динамика выручки", "t", records=[{"total": 10}, {"total": 11}]) == "table"
    assert select_chart("Динамика выручки", "t", records=MONTHS) == "line"


def test_histogram_takes_the_column_named_in_the_question() -> None:
    titles = {"price": "Цена", "qty": "Количество"}
    assert select_chart("Распределение цен", "t", records=ORDERS, titles=titles) == "histogram"
    assert select_chart("Распределение qty", "t", records=ORDERS) == "histogram"
    assert select_chart("Распределение заказов", "t", records=ORDERS, titles=titles) == "table"


@pytest.mark.parametrize(
    ("question", "titles", "expected"),
    [
        ("Распределение цен", {"price": "Цена"}, "price"),
        ("Как распределены суммы заказов", {"qty": "Сумма заказа"}, "qty"),
        ("Распределение qty", {}, "qty"),
        ("Распределение заказов", {}, None),
    ],
)
def test_histogram_column(question: str, titles: dict, expected: str | None) -> None:
    assert histogram_column(question, infer_field_types(ORDERS), titles) == expected


def test_unfit_heuristic_falls_back_to_table() -> None:
    assert select_chart("Топ товаров", "t", records=MANY_CATEGORIES) == "table"
    assert select_chart("Распределение чеков", "t", records=REGIONS) == "table"
    assert select_chart("Распределение чеков", "t", records=SAMPLES) == "histogram"
    assert select_chart("Топ товаров", "t", records=[]) == "table"


def test_time_dependence_question_is_a_line_even_if_planner_said_table() -> None:
    question = "создай график зависимости потребления от даты"

    assert select_chart(question, "t", "auto", records=DAILY) == "line"
    assert select_chart(question, "t", "table", records=DAILY) == "line"
    assert select_chart(question, "t", "bar", records=DAILY) == "line"


def test_explicit_table_request_is_kept() -> None:
    assert select_chart("покажи таблицей", "t", "table", records=DAILY) == "table"
    assert select_chart("таблица с графиком", "t", "table", records=DAILY) == "table"
    assert select_chart("покажи график, а не таблицу", "t", "table", records=DAILY) == "line"


def test_dependence_without_date_is_scatter() -> None:
    assert select_chart("зависимость спроса от цены", "t", records=PAIRS) == "scatter"


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
