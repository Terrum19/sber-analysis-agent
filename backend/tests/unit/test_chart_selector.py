import datetime as dt
from decimal import Decimal

import pytest

from app.core.viz.chart_selector import (
    fits,
    fits_columns,
    histogram_column,
    is_single_number,
    select_chart,
)
from app.core.viz.vega_spec_builder import infer_field_types

MONTHS = [{"month": f"2024-{month:02d}", "revenue": month * 10} for month in range(1, 13)]
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


def test_fits_heatmap_cohort_and_grid() -> None:
    cohort = [
        {"cohort_week": dt.date(2024, 1, 1), "week_number": 0, "retention": 1.0},
        {"cohort_week": dt.date(2024, 1, 8), "week_number": 0, "retention": 1.0},
    ]
    types_cohort = infer_field_types(cohort)
    assert fits("heatmap", cohort, types_cohort) is True
    assert fits_columns("heatmap", cohort, types_cohort, "cohort_week", "week_number") is True

    grid = [{"weekday": 1, "hour": 10, "orders": 5}]
    types_grid = infer_field_types(grid)
    assert fits("heatmap", grid, types_grid) is True
    assert fits_columns("heatmap", grid, types_grid, "weekday", "hour") is True


def test_fits_columns_heatmap_metric_handling() -> None:
    # 2 columns: region and revenue (revenue is float metric)
    records_2 = [{"region": "msk", "revenue": 10.5}, {"region": "spb", "revenue": 20.0}]
    types_2 = infer_field_types(records_2)
    assert fits_columns("heatmap", records_2, types_2, "region", "revenue") is False
    assert fits_columns("heatmap", records_2, types_2, "revenue", "region") is False
    assert fits_columns("heatmap", records_2, types_2, None, "region") is False
    assert fits_columns("heatmap", records_2, types_2, "region", "region") is False

    # 3 columns: region, segment, revenue
    records_3 = [{"region": "msk", "segment": "b2b", "revenue": 10.5}]
    types_3 = infer_field_types(records_3)
    # y is metric, color is None -> replacement segment found -> True
    assert fits_columns("heatmap", records_3, types_3, "region", "revenue", color=None) is True
    # y is metric, color already taken -> False
    assert fits_columns("heatmap", records_3, types_3, "region", "revenue", color="orders") is False


def test_fits_columns_box_kpi_table() -> None:
    records = [{"val": 10.5}]
    types = infer_field_types(records)
    assert fits_columns("box", records, types, None, "val") is True
    assert fits_columns("box", records, types, None, "missing") is False
    assert fits_columns("kpi", [{"total": 42}], {"total": "quantitative"}, None, "total") is True
    assert (
        fits_columns(
            "kpi",
            [{"total": 42}, {"total": 43}],
            {"total": "quantitative"},
            None,
            "total",
        )
        is False
    )
    assert fits_columns("table", records, types, None, None) is True
    assert fits_columns("unknown_type", records, types, None, None) is False


def test_fits_and_fits_columns_wide_matrix() -> None:
    wide_records = [
        {"male": 1.0, "age": 0.14, "isAlive": -0.15},
        {"male": 0.14, "age": 1.0, "isAlive": -0.31},
        {"male": -0.15, "age": -0.31, "isAlive": 1.0},
    ]
    types = infer_field_types(wide_records)
    assert fits("heatmap", wide_records, types) is True
    assert fits("bar", wide_records, types) is False
    assert fits_columns("heatmap", wide_records, types, None, None) is True
    assert fits_columns("bar", wide_records, types, "male", "age") is False
    assert select_chart("Построй корреляционную матрицу", "t", records=wide_records) == "heatmap"
    assert select_chart("построй график", "t", "auto", records=wide_records) == "heatmap"
    assert select_chart("построй график", "t", "bar", records=wide_records) == "heatmap"
    assert (
        select_chart("построй корреляцию признаков", "t", "auto", records=wide_records) == "heatmap"
    )

    # Columns table (like Screenshot 2) with unique column and long text is rejected from heatmap
    col_records = [
        {
            "Колонка": f"col_{i}",
            "Тип": "текст" if i % 2 == 0 else "число",
            "Заполнено": 80.0 + i,
            "Значения": "очень длинное описание значений колонки превышающее 50 символов " * 2,
        }
        for i in range(5)
    ]
    col_types = infer_field_types(col_records)
    assert fits("heatmap", col_records, col_types) is False
