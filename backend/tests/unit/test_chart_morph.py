"""Unit-тесты морфинга графиков без нового вызова LLM."""

import pytest

from app.core.viz.chart_morph import AUTO, MORPH_MAP, detect_morph, morph, morph_candidates
from app.core.viz.vega_spec_builder import CHART_TYPES

SALES = [
    {"month": "2024-01-01", "region": "msk", "revenue": 10.0},
    {"month": "2024-02-01", "region": "spb", "revenue": 12.0},
]


def test_morph_bar_to_line() -> None:
    spec = morph("bar", "line", [{"x": 1, "y": 2}])
    assert spec["mark"] == "line"


def test_morph_illegal_falls_back() -> None:
    spec = morph("scatter", "bar", [{"x": 1, "y": 2}])
    assert spec["mark"] in {"bar", "line"}


def test_morph_keeps_data_and_titles() -> None:
    spec = morph("bar", "table", SALES, {"revenue": "Выручка"}, "temp_result_v3")
    assert spec["usermeta"]["chart_type"] == "table"
    assert spec["usermeta"]["result_table"] == "temp_result_v3"
    assert spec["data"]["values"] == SALES
    assert spec["usermeta"]["columns"][2]["title"] == "Выручка"


def test_auto_morph_picks_first_different_view() -> None:
    assert morph("bar", AUTO, SALES)["usermeta"]["chart_type"] == "line"
    assert morph("line", AUTO, SALES)["usermeta"]["chart_type"] == "bar"


def test_auto_morph_skips_views_equal_to_current() -> None:
    data = [{"amount": 1.0}, {"amount": 2.0}]
    assert morph("histogram", AUTO, data)["usermeta"]["chart_type"] == "box"
    assert morph("box", AUTO, data)["usermeta"]["chart_type"] == "histogram"
    assert morph("bar", AUTO, [{"region": "msk"}])["usermeta"]["chart_type"] == "line"


def test_auto_morph_from_unknown_type_rebuilds_it() -> None:
    assert morph_candidates("pie", AUTO) == ["pie"]
    assert morph("pie", AUTO, SALES)["usermeta"]["chart_type"] == "bar"


def test_every_type_can_be_morphed_to_table() -> None:
    assert set(MORPH_MAP) == set(CHART_TYPES)
    for current, targets in MORPH_MAP.items():
        assert "table" in targets or current == "table"
        assert set(targets) <= set(CHART_TYPES)


def test_explicit_target_wins() -> None:
    assert morph_candidates("bar", "scatter") == ["scatter"]
    assert morph_candidates("bar", AUTO) == MORPH_MAP["bar"]


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("А теперь покажи то же самое в другом виде", AUTO),
        ("Покажи по-другому", AUTO),
        ("Нужен другой график", AUTO),
        ("Смени тип графика", AUTO),
        ("Сделай линейным графиком", "line"),
        ("Сделай таблицей", "table"),
        ("Покажи это в виде таблицы", "table"),
        ("А теперь столбчатой диаграммой", "bar"),
        ("Перестрой как гистограмму", "histogram"),
        ("Замени на диаграмму рассеяния", "scatter"),
        ("Сделай ящик с усами", "box"),
        ("Покажи то же самое тепловой картой", "heatmap"),
        ("Сделай с заливкой", "area"),
        ("Покажи то же самое в другом виде, например таблицей", "table"),
    ],
)
def test_detects_view_change(question: str, expected: str) -> None:
    assert detect_morph(question) == expected


@pytest.mark.parametrize(
    "question",
    [
        "Покажи динамику выручки по месяцам",
        "Построй гистограмму распределения суммы",
        "Какая таблица содержит клиентов?",
        "Сравни регионы по выручке",
        "Сколько заказов в Москве?",
        "Сделай группировку по столбцу region",
        "А теперь посчитай сумму по столбцу amount",
    ],
)
def test_new_questions_are_not_morphs(question: str) -> None:
    assert detect_morph(question) is None


def test_auto_morph_keeps_current_view_when_nothing_else_fits() -> None:
    spec = morph("table", AUTO, [])
    assert spec["usermeta"]["chart_type"] == "table"
    assert spec["encoding"] == {}
