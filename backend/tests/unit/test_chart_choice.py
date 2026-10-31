"""Выбор графика агентом: явные колонки в спеке, проверка выбора, топ-N по limit."""

import pytest
from pydantic import ValidationError

from app.core.dashboards.models import DashboardTile
from app.core.dashboards.recalculate import rebuild_spec, respec
from app.core.data.duckdb_manager import DuckDBManager
from app.core.viz.chart_choice import limited, rejection, top_rows
from app.core.viz.vega_spec_builder import build_vega_spec
from app.models.chart_choice import AnswerChart, ChartPick

STORES = [
    {"store_id": store, "category": category, "revenue": float(store * 10 + len(category))}
    for store in range(1, 6)
    for category in ("еда", "техника")
]


def test_integer_key_on_bar_axis_is_a_category() -> None:
    spec = build_vega_spec("bar", data=STORES, x="store_id", y="revenue")
    assert spec["mark"] == "bar"
    assert spec["encoding"]["x"]["field"] == "store_id"
    assert spec["encoding"]["x"]["type"] == "nominal"
    # Без выбора первым числом был бы сам ключ store_id.
    assert spec["encoding"]["y"]["field"] == "revenue"
    assert "color" not in spec["encoding"]


def test_bar_color_splits_bars_side_by_side() -> None:
    spec = build_vega_spec("bar", data=STORES, x="store_id", y="revenue", color="category")
    assert spec["encoding"]["color"]["field"] == "category"
    assert spec["encoding"]["xOffset"] == {"field": "category", "type": "nominal"}
    assert spec["usermeta"]["color"] == "category"


def test_line_by_year_number_is_ordinal_with_named_series() -> None:
    rows = [
        {"year": year, "region": region, "store_id": 1, "revenue": float(year)}
        for year in (2022, 2023, 2024)
        for region in ("msk", "spb")
    ]
    spec = build_vega_spec("line", data=rows, x="year", y="revenue", color="region")
    assert spec["encoding"]["x"] == {"field": "year", "type": "ordinal", "title": "year"}
    assert spec["encoding"]["y"]["field"] == "revenue"
    assert spec["encoding"]["color"]["field"] == "region"


def test_named_columns_without_color_draw_one_series() -> None:
    rows = [{"product": f"p{i}", "revenue": float(i), "quantity": float(i * 2)} for i in range(25)]
    spec = build_vega_spec("scatter", data=rows, x="quantity", y="revenue")
    assert spec["encoding"]["x"]["field"] == "quantity"
    assert spec["encoding"]["y"]["field"] == "revenue"
    # Без выбора колонок цвет был бы по product — 25 цветов.
    assert "color" not in spec["encoding"]


def test_heatmap_takes_two_categories_and_the_number_in_color() -> None:
    spec = build_vega_spec("heatmap", data=STORES, x="store_id", y="category", color="revenue")
    assert spec["encoding"]["x"]["field"] == "store_id"
    assert spec["encoding"]["y"]["field"] == "category"
    assert spec["encoding"]["color"]["field"] == "revenue"


def test_unknown_columns_are_left_out_of_the_choice() -> None:
    spec = build_vega_spec("bar", data=STORES, x="nope", y="revenue")
    assert spec["encoding"]["x"]["field"] == "category"
    assert "x" not in spec["usermeta"]
    assert spec["usermeta"]["y"] == "revenue"


def test_recalculated_tile_keeps_the_chosen_columns() -> None:
    spec = build_vega_spec("bar", "temp_result_v1", data=STORES, x="store_id", y="revenue")
    tile = DashboardTile(title="Магазины", spec=spec, result_table="temp_result_v1")
    rebuilt = rebuild_spec(tile, STORES)
    assert rebuilt["encoding"]["x"]["field"] == "store_id"
    assert rebuilt["usermeta"]["x"] == "store_id"


def test_rejection_explains_what_does_not_fit() -> None:
    many = [{"store_id": store, "revenue": float(store)} for store in range(60)]
    assert rejection(ChartPick(chart_type="bar", x="store"), many) == (
        "колонки 'store' нет в результате"
    )
    assert "задай limit" in rejection(ChartPick(chart_type="bar", x="store_id"), many)
    assert rejection(ChartPick(chart_type="bar", x="store_id", y="revenue", limit=10), many) is None
    # y столбцов — число, а не категория.
    assert rejection(ChartPick(chart_type="bar", x="store_id", y="category"), STORES) == (
        "bar не подходит к x=store_id (quantitative), y=category (nominal) на 10 строках"
    )
    assert rejection(ChartPick(chart_type="scatter", x="store_id", y="store_id"), many) is not None
    assert rejection(ChartPick(chart_type="kpi"), many) == (
        "kpi — только когда результат одно число"
    )
    assert rejection(ChartPick(chart_type="bar"), []) == "результат пуст"
    # Без колонок — проверка по первым категории и числу, как раньше.
    assert rejection(ChartPick(chart_type="bar"), STORES) is None

    # Heatmap: y метрика при пустом color переносится в color, если есть вторая категория
    choice_repaired = ChartPick(chart_type="heatmap", x="store_id", y="revenue", color=None)
    assert rejection(choice_repaired, STORES) is None
    assert choice_repaired.y == "category"
    assert choice_repaired.color == "revenue"

    # Heatmap: y метрика без второй категории возвращает понятный отказ
    two_cols = [{"region": "msk", "revenue": 10.5}]
    choice_bad_y = ChartPick(chart_type="heatmap", x="region", y="revenue", color=None)
    assert rejection(choice_bad_y, two_cols) == (
        "heatmap: y='revenue' — числовая метрика; x и y должны быть "
        "категориями (или датой и номером периода), а число — в color"
    )

    # Heatmap: y метрика при уже занятом color возвращает отказ
    choice_busy_color = ChartPick(chart_type="heatmap", x="store_id", y="revenue", color="category")
    assert "y='revenue' — числовая метрика" in rejection(choice_busy_color, STORES)

    # Heatmap: x метрика возвращает отказ
    choice_bad_x = ChartPick(chart_type="heatmap", x="revenue", y="category")
    assert rejection(choice_bad_x, STORES) == (
        "heatmap: x='revenue' — числовая метрика; x и y должны быть "
        "категориями (или датой и номером периода), а число — в color"
    )


def test_limit_keeps_the_top_rows_by_y() -> None:
    many = [{"store_id": store, "revenue": float(store % 7)} for store in range(40)]
    choice = ChartPick(chart_type="bar", x="store_id", y="revenue", limit=3)
    assert [row["revenue"] for row in limited(many, choice)] == [6.0, 6.0, 6.0]

    db = DuckDBManager(":memory:")
    db.execute("CREATE TABLE sales AS SELECT i AS store_id, i % 7 AS revenue FROM range(40) t(i)")
    table = top_rows(db, "sales", choice)
    assert table != "sales"
    assert [row["revenue"] for row in db.to_records(table)] == [6, 6, 6]
    assert top_rows(db, "sales", ChartPick(chart_type="bar", x="store_id")) == "sales"
    db.close()


def test_model_noise_in_limit_and_type_is_normalized() -> None:
    assert ChartPick(limit="10").limit == 10
    assert ChartPick(limit=0).limit is None
    assert ChartPick(limit="все").limit is None
    assert ChartPick(chart_type=" Pie ").chart_type == "table"
    # Без вида ответ не проходит схему: молчание модели — не выбор таблицы.
    with pytest.raises(ValidationError):
        AnswerChart.model_validate({"step_id": 1})


def test_many_categories_lie_down_sorted_by_value() -> None:
    regions = [{"region": f"Регион {number}", "revenue": float(number)} for number in range(31)]
    spec = build_vega_spec("bar", data=regions, x="region", y="revenue")
    assert spec["encoding"]["y"]["field"] == "region"
    assert spec["encoding"]["y"]["sort"] == "-x"
    assert spec["encoding"]["x"]["field"] == "revenue"
    assert spec["height"] == {"step": 18}
    assert rejection(ChartPick(chart_type="bar", x="region", y="revenue"), regions) is None
    # Немного категорий и даты — стоя, в порядке данных.
    few = build_vega_spec("bar", data=regions[:5], x="region", y="revenue")
    assert few["encoding"]["x"]["field"] == "region"
    assert "height" not in few


def test_tile_switches_view_from_its_own_rows() -> None:
    table = build_vega_spec("table", "temp_result_v1", data=STORES[:4])
    bar = respec(table, "bar")
    assert bar["mark"] == "bar"
    assert "transform" not in bar
    assert bar["encoding"]["x"]["field"] == "category"
    assert bar["data"]["values"] == table["data"]["values"]
    assert bar["usermeta"]["result_table"] == "temp_result_v1"
    assert respec({"mark": "bar"}, "line") == {"mark": "bar"}


def test_rejection_accepts_wide_matrix_for_heatmap() -> None:
    records = [
        {"male": 1.0, "age": 0.14, "isAlive": -0.15},
        {"male": 0.14, "age": 1.0, "isAlive": -0.31},
        {"male": -0.15, "age": -0.31, "isAlive": 1.0},
    ]
    assert rejection(ChartPick(chart_type="heatmap"), records) is None
    rej = rejection(ChartPick(chart_type="bar", x="male", y="age"), records)
    assert rej is not None
    assert "матрицу" in rej
    assert "heatmap" in rej
