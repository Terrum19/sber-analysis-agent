"""Unit-тесты автосборки Vega-Lite спецификаций из результата шага."""

import datetime as dt
import json
import re
import uuid
from decimal import Decimal

import pytest
import vl_convert as vlc

from app.core.data.duckdb_manager import DuckDBManager
from app.core.viz.vega_spec_builder import (
    CHART_TYPES,
    COUNT_TITLE,
    SPEC_ROW_LIMIT,
    VEGA_LITE_SCHEMA,
    _temporal_step,
    build_vega_spec,
    chart_title,
    column_aliases,
    detect_wide_matrix,
    field_ref,
    heatmap_axis_candidates,
    heatmap_metric_candidate,
    infer_field_type,
    infer_field_types,
    is_correlation_matrix,
    is_fractional_column,
    is_heatmap_axis,
    is_integer_column,
    is_integer_value,
    ordered_column,
    to_json_value,
)

SALES = [
    {"month": dt.date(2024, month, 1), "region": region, "revenue": Decimal(month), "orders": month}
    for month in (1, 2, 3)
    for region in ("msk", "spb")
]


def encoding_of(chart_type: str, data: list[dict], **kwargs) -> dict:
    return build_vega_spec(chart_type, "temp_result_v1", data=data, **kwargs)["encoding"]


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE temp_result_v1 AS SELECT * FROM (VALUES "
        "(DATE '2024-01-01', 'msk', 10.5), (DATE '2024-02-01', 'msk', 12.0)) "
        "AS rows(month, region, revenue)"
    )
    yield manager
    manager.close()


def test_loads_rows_from_result_table(db) -> None:
    spec = build_vega_spec("line", "temp_result_v1", db=db)
    assert spec["$schema"] == VEGA_LITE_SCHEMA
    assert spec["data"]["values"][0] == {"month": "2024-01-01", "region": "msk", "revenue": 10.5}
    assert spec["encoding"]["x"]["field"] == "month"
    assert spec["encoding"]["x"]["type"] == "temporal"
    assert spec["encoding"]["y"]["field"] == "revenue"
    assert spec["encoding"]["color"]["field"] == "region"
    assert spec["usermeta"]["result_table"] == "temp_result_v1"
    json.dumps(spec, allow_nan=False)


def test_row_limit_is_passed_to_duckdb() -> None:
    class Recorder:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int]] = []

        def to_records(self, table: str, limit: int = 100) -> list[dict]:
            self.calls.append((table, limit))
            return []

    recorder = Recorder()
    build_vega_spec("bar", "temp_result_v7", db=recorder)
    assert recorder.calls == [("temp_result_v7", SPEC_ROW_LIMIT)]


def test_without_db_and_data_the_spec_is_empty() -> None:
    spec = build_vega_spec("bar", "temp_result_v1")
    assert spec["data"] == {"values": []}
    assert spec["encoding"] == {}
    assert spec["usermeta"]["chart_type"] == "table"


@pytest.mark.parametrize("chart_type", [kind for kind in CHART_TYPES if kind != "kpi"])
def test_every_type_fills_data_and_encoding(chart_type: str) -> None:
    spec = build_vega_spec(chart_type, "temp_result_v1", data=SALES, title="Выручка")
    assert spec["usermeta"]["chart_type"] == chart_type
    assert spec["usermeta"]["requested_type"] == chart_type
    assert len(spec["data"]["values"]) == len(SALES)
    assert {"x", "y", "tooltip"} <= set(spec["encoding"])
    assert spec["title"] == "Выручка"
    json.dumps(spec, allow_nan=False)


def test_unknown_type_falls_back_to_bar() -> None:
    spec = build_vega_spec("pie", None, data=SALES)
    assert spec["mark"] == "bar"
    assert spec["usermeta"]["requested_type"] == "bar"
    assert "title" not in spec


def test_bar_uses_category_and_sum() -> None:
    encoding = encoding_of("bar", SALES, titles={"revenue": "Выручка", "region": "Регион"})
    assert encoding["x"] == {"field": "region", "type": "nominal", "title": "Регион", "sort": None}
    assert encoding["y"] == {
        "field": "revenue",
        "type": "quantitative",
        "title": "Выручка",
        "aggregate": "sum",
    }
    assert [channel["field"] for channel in encoding["tooltip"]] == ["region", "revenue"]
    assert "sort" not in encoding["tooltip"][0]


def test_bar_over_dates_is_ordinal_and_counts_without_metric() -> None:
    encoding = encoding_of("bar", [{"day": dt.date(2024, 1, 1)}, {"day": dt.date(2024, 1, 2)}])
    assert encoding["x"]["type"] == "ordinal"
    assert encoding["y"] == {"aggregate": "count", "type": "quantitative", "title": COUNT_TITLE}


def test_bar_without_category_becomes_histogram() -> None:
    spec = build_vega_spec("bar", None, data=[{"amount": 1.0}, {"amount": 2.0}])
    assert spec["usermeta"]["chart_type"] == "histogram"
    assert spec["encoding"]["x"]["bin"] == {"maxbins": 20}


def test_line_prefers_named_order_column() -> None:
    encoding = encoding_of("line", [{"year": 2020, "sales": 3}, {"year": 2021, "sales": 5}])
    assert encoding["x"] == {"field": "year", "type": "ordinal", "title": "year"}
    assert encoding["y"]["field"] == "sales"


def test_line_over_category_and_counts() -> None:
    encoding = encoding_of("line", [{"region": "msk"}, {"region": "spb"}])
    assert encoding["x"]["type"] == "ordinal"
    assert encoding["y"]["aggregate"] == "count"
    assert "color" not in encoding


def test_line_over_two_numbers() -> None:
    encoding = encoding_of("line", [{"left": 1, "right": 2}])
    assert encoding["x"]["type"] == "quantitative"
    assert encoding["y"]["field"] == "right"


def test_line_with_single_number_becomes_histogram() -> None:
    spec = build_vega_spec("line", None, data=[{"amount": 1}])
    assert spec["usermeta"]["chart_type"] == "histogram"


def test_line_does_not_colour_by_second_date() -> None:
    data = [{"start": "2024-01-01", "finish": "2024-02-01", "amount": 1}]
    encoding = encoding_of("line", data)
    assert encoding["x"]["field"] == "start"
    assert "color" not in encoding


def test_area_reuses_line_encoding() -> None:
    spec = build_vega_spec("area", None, data=SALES)
    assert spec["mark"] == "area"
    assert spec["encoding"]["x"]["field"] == "month"


def test_histogram_bins_first_number() -> None:
    encoding = encoding_of("histogram", SALES)
    assert encoding["x"]["field"] == "revenue"
    assert encoding["x"]["bin"] == {"maxbins": 20}
    assert encoding["y"]["aggregate"] == "count"


def test_histogram_without_numbers_counts_categories() -> None:
    spec = build_vega_spec("histogram", None, data=[{"region": "msk"}])
    assert spec["usermeta"]["chart_type"] == "bar"
    assert spec["encoding"]["y"]["aggregate"] == "count"


def test_scatter_uses_two_numbers_and_colour() -> None:
    encoding = encoding_of("scatter", SALES)
    assert encoding["x"]["field"] == "revenue"
    assert encoding["y"]["field"] == "orders"
    assert encoding["color"]["field"] == "region"
    assert len(encoding["tooltip"]) == 4


def test_scatter_without_category_has_no_colour() -> None:
    encoding = encoding_of("scatter", [{"left": 1, "right": 2}])
    assert "color" not in encoding


def test_scatter_with_one_number_becomes_bar() -> None:
    spec = build_vega_spec("scatter", None, data=[{"region": "msk", "amount": 1}])
    assert spec["usermeta"]["chart_type"] == "bar"


def test_box_by_category() -> None:
    encoding = encoding_of("box", SALES)
    assert encoding["x"]["field"] == "region"
    assert encoding["y"]["field"] == "revenue"


def test_box_without_category_uses_datum() -> None:
    encoding = encoding_of("box", [{"amount": 1}], titles={"amount": "Сумма"})
    assert encoding["x"] == {"datum": "Сумма", "type": "nominal", "title": None}


def test_box_without_numbers_becomes_bar() -> None:
    spec = build_vega_spec("box", None, data=[{"region": "msk"}])
    assert spec["usermeta"]["chart_type"] == "bar"


def test_heatmap_sums_metric_over_two_categories() -> None:
    encoding = encoding_of("heatmap", SALES)
    assert encoding["x"]["field"] == "region"
    assert encoding["y"]["field"] == "month"
    assert encoding["color"]["aggregate"] == "sum"
    assert "scale" not in encoding["tooltip"][2]


def test_heatmap_counts_without_metric() -> None:
    encoding = encoding_of("heatmap", [{"region": "msk", "segment": "b2b"}])
    assert encoding["color"]["aggregate"] == "count"


@pytest.mark.parametrize("data", [[{"region": "msk", "amount": 1}], [{"amount": 1}]])
def test_heatmap_needs_two_categories(data: list[dict]) -> None:
    spec = build_vega_spec("heatmap", None, data=data)
    assert spec["usermeta"]["chart_type"] != "heatmap"


def test_heatmap_cohort_auto_without_xy() -> None:
    cohort = [
        {"cohort_week": dt.date(2024, 1, 1), "week_number": 0, "retention": 1.0},
        {"cohort_week": dt.date(2024, 1, 8), "week_number": 0, "retention": 1.0},
        {"cohort_week": dt.date(2024, 1, 1), "week_number": 1, "retention": 0.65},
    ]
    spec = build_vega_spec("heatmap", data=cohort)
    assert spec["usermeta"]["chart_type"] == "heatmap"
    assert spec["encoding"]["x"]["field"] == "cohort_week"
    assert spec["encoding"]["x"]["type"] == "ordinal"
    assert spec["encoding"]["x"]["timeUnit"] == "yearmonthdate"
    assert spec["encoding"]["x"]["axis"]["format"] == "%d.%m.%Y"
    assert spec["encoding"]["y"]["field"] == "week_number"
    assert spec["encoding"]["y"]["type"] == "ordinal"
    assert spec["encoding"]["color"]["field"] == "retention"
    assert spec["encoding"]["color"]["type"] == "quantitative"


def test_heatmap_cohort_explicit_xy() -> None:
    cohort = [
        {"cohort_week": dt.date(2024, 1, 1), "week_number": 0, "retention": 1.0},
        {"cohort_week": dt.date(2024, 1, 8), "week_number": 0, "retention": 1.0},
    ]
    spec = build_vega_spec(
        "heatmap", data=cohort, x="cohort_week", y="week_number", color="retention"
    )
    assert spec["usermeta"]["chart_type"] == "heatmap"
    assert spec["encoding"]["x"]["field"] == "cohort_week"
    assert spec["encoding"]["x"]["timeUnit"] == "yearmonthdate"
    assert spec["encoding"]["x"]["axis"]["format"] == "%d.%m.%Y"
    assert spec["encoding"]["y"]["field"] == "week_number"
    assert spec["encoding"]["color"]["field"] == "retention"


def test_heatmap_weekday_hour_orders_all_int() -> None:
    grid = [{"weekday": w, "hour": h, "orders": w * 24 + h} for w in range(7) for h in range(24)]
    spec = build_vega_spec("heatmap", data=grid)
    assert spec["usermeta"]["chart_type"] == "heatmap"
    assert spec["encoding"]["x"]["field"] == "weekday"
    assert spec["encoding"]["y"]["field"] == "hour"
    assert spec["encoding"]["color"]["field"] == "orders"


def test_heatmap_y_metric_moved_to_color_when_color_none() -> None:
    data = [
        {"region": "msk", "segment": "b2b", "revenue": 100.5},
        {"region": "spb", "segment": "b2c", "revenue": 50.2},
    ]
    spec = build_vega_spec("heatmap", data=data, x="region", y="revenue", color=None)
    assert spec["usermeta"]["chart_type"] == "heatmap"
    assert spec["encoding"]["y"]["field"] != "revenue"
    assert spec["encoding"]["y"]["field"] == "segment"
    assert spec["encoding"]["color"]["field"] == "revenue"
    assert spec["usermeta"]["y"] == "segment"
    assert spec["usermeta"]["color"] == "revenue"


def test_heatmap_y_metric_with_color_set_clears_y() -> None:
    data = [
        {"region": "msk", "revenue": 100.5},
        {"region": "spb", "revenue": 50.2},
    ]
    spec = build_vega_spec("heatmap", data=data, x="region", y="revenue", color="revenue")
    assert spec["usermeta"]["chart_type"] == "bar"


def test_heatmap_x_metric_clears_x() -> None:
    data = [
        {"region": "msk", "revenue": 100.5},
        {"region": "spb", "revenue": 50.2},
    ]
    spec = build_vega_spec("heatmap", data=data, x="revenue", y="region")
    assert spec["usermeta"]["chart_type"] == "bar"


def test_heatmap_temporal_step_helpers() -> None:
    assert _temporal_step([]) == ("yearmonthdate", "%d.%m.%Y")
    assert _temporal_step(["2024-01", "2024-02"]) == ("yearmonth", "%m.%Y")
    assert _temporal_step([dt.datetime(2024, 1, 1, 10, 0), dt.datetime(2024, 1, 8, 10, 0)]) == (
        "yearmonthdate",
        "%d.%m.%Y",
    )
    assert _temporal_step(["invalid-date"]) == ("yearmonthdate", "%d.%m.%Y")
    assert _temporal_step([dt.date(2024, 1, 1), dt.date(2024, 1, 15)]) == (
        "yearmonthdate",
        "%d.%m.%Y",
    )
    assert _temporal_step([dt.date(2024, 1, 1)], "month") == ("yearmonth", "%m.%Y")
    assert _temporal_step([dt.date(2024, 1, 1), dt.date(2024, 2, 1)], "order_period") == (
        "yearmonth",
        "%m.%Y",
    )
    assert _temporal_step([dt.date(2024, 1, 1)], "week") == ("yearmonthdate", "%d.%m.%Y")
    assert _temporal_step([dt.date(2024, 1, 1)], "some_col") == ("yearmonthdate", "%d.%m.%Y")


def test_integer_and_fractional_helpers() -> None:
    assert is_integer_value(1) is True
    assert is_integer_value(1.0) is False
    assert is_integer_value(1.5) is False
    assert is_integer_value(True) is False
    assert is_integer_value(None) is False
    assert is_integer_value("1") is False
    assert is_integer_value(float("nan")) is False

    records = [{"i": 1, "f": 1.5, "b": True, "large": idx} for idx in range(60)]
    assert is_integer_column(records, "i") is True
    assert is_integer_column(records, "f") is False
    assert is_integer_column([], "i") is False
    assert is_fractional_column(records, "f") is True
    assert is_fractional_column(records, "i") is False

    types = {"i": "quantitative", "f": "quantitative", "large": "quantitative", "cat": "nominal"}
    assert is_heatmap_axis(records, types, None) is False
    assert is_heatmap_axis(records, types, "unknown") is False
    assert is_heatmap_axis(records, types, "cat") is True
    assert is_heatmap_axis(records, types, "i") is True
    assert is_heatmap_axis(records, types, "f") is False
    assert is_heatmap_axis(records, types, "large") is False

    assert heatmap_axis_candidates(records, types) == ["cat", "i"]
    assert heatmap_metric_candidate(records, types) == "f"
    assert heatmap_metric_candidate(records, {"cat": "nominal"}) is None

    records_large_int = [{"large": idx, "other": 1} for idx in range(60)]
    types_large = {"large": "quantitative", "other": "quantitative"}
    assert heatmap_metric_candidate(records_large_int, types_large) == "large"

    records_order = [{"hour": 1, "other": 2}]
    types_order = {"hour": "quantitative", "other": "quantitative"}
    assert heatmap_metric_candidate(records_order, types_order) == "other"
    assert heatmap_metric_candidate(records_order, {"hour": "quantitative"}) == "hour"


def test_line_quantitative_not_order_name() -> None:
    data = [{"qty": 1, "revenue": 10.0}, {"qty": 2, "revenue": 20.0}]
    spec = build_vega_spec("line", data=data, x="qty", y="revenue")
    assert spec["encoding"]["x"]["field"] == "qty"
    assert spec["encoding"]["x"]["type"] == "quantitative"


def test_table_folds_columns() -> None:
    spec = build_vega_spec("table", None, data=[{"a.b": 1, "region": "msk"}])
    assert spec["mark"] == "text"
    assert spec["transform"][1]["fold"] == ["a\\.b", "region"]
    assert spec["encoding"]["text"]["field"] == "_value"
    assert spec["usermeta"]["columns"][0] == {
        "field": "a.b",
        "title": "a.b",
        "type": "quantitative",
    }


def test_empty_table() -> None:
    spec = build_vega_spec("table", None, data=[])
    assert spec["encoding"] == {}
    assert "transform" not in spec


def test_field_ref_escapes_special_characters() -> None:
    assert field_ref("a.b[0]\\") == "a\\.b\\[0\\]\\\\"
    assert field_ref("выручка") == "выручка"


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([], "nominal"),
        ([None], "nominal"),
        ([dt.date(2024, 1, 1), None, dt.datetime(2024, 1, 1, 10)], "temporal"),
        ([1, 2.5, Decimal("3.1")], "quantitative"),
        ([True, False], "nominal"),
        (["2024-01", "2024-02-03", "2024-02-03 10:00:00.5"], "temporal"),
        (["2024-01", "msk"], "nominal"),
        ([1, "msk"], "nominal"),
    ],
)
def test_infer_field_type(values: list, expected: str) -> None:
    assert infer_field_type(values) == expected


def test_infer_field_types_keeps_column_order_and_gaps() -> None:
    types = infer_field_types([{"region": "msk"}, {"amount": 1, "region": None}])
    assert types == {"region": "nominal", "amount": "quantitative"}


def test_to_json_value() -> None:
    marker = uuid.UUID(int=1)
    assert to_json_value(None) is None
    assert to_json_value(True) is True
    assert to_json_value(3) == 3
    assert to_json_value("msk") == "msk"
    assert to_json_value(1.5) == 1.5
    assert to_json_value(float("nan")) is None
    assert to_json_value(float("inf")) is None
    assert to_json_value(Decimal("2.5")) == 2.5
    assert to_json_value(Decimal("NaN")) is None
    assert to_json_value(dt.date(2024, 1, 5)) == "2024-01-05"
    assert to_json_value(dt.datetime(2024, 1, 5, 10, 30)) == "2024-01-05T10:30:00"
    assert to_json_value(dt.time(10, 30)) == "10:30:00"
    assert to_json_value(marker) == str(marker)


def test_column_aliases_from_passport() -> None:
    passport = {
        "glossary": {"amount": "сумма", "blank": "  "},
        "titles": {"region": "регион", "empty": "", "odd": 5, "absent": None},
        "aliases": {"day": ["день"]},
    }
    assert column_aliases(passport) == {"amount": "Сумма", "region": "Регион"}
    assert column_aliases({"glossary": None}) == {}
    assert column_aliases({}) == {}


def test_titles_use_aliases() -> None:
    encoding = encoding_of("bar", SALES, titles=column_aliases({"glossary": {"region": "регион"}}))
    assert encoding["x"]["title"] == "Регион"
    assert encoding["y"]["title"] == "revenue"


def test_kpi_shows_one_number_with_the_title() -> None:
    spec = build_vega_spec(
        "kpi",
        "temp_result_v1",
        data=[{"total": Decimal("1250.5")}],
        titles={"total": "Выручка"},
        title="Выручка за квартал",
    )
    assert spec["usermeta"]["chart_type"] == "kpi"
    assert spec["mark"] == {"type": "text", "fontSize": 48}
    assert spec["encoding"]["text"] == {
        "field": "total",
        "type": "quantitative",
        "title": "Выручка",
        "format": ",.2~f",
    }
    assert spec["encoding"]["tooltip"] == [
        {"field": "total", "type": "quantitative", "title": "Выручка"}
    ]
    assert spec["title"] == "Выручка за квартал"
    assert spec["data"]["values"] == [{"total": 1250.5}]
    json.dumps(spec, allow_nan=False)


def test_kpi_takes_the_first_row_and_first_metric() -> None:
    spec = build_vega_spec("kpi", "temp_result_v1", data=SALES)
    assert spec["usermeta"]["chart_type"] == "kpi"
    assert spec["encoding"]["text"]["field"] == "revenue"
    assert spec["transform"] == [
        {"window": [{"op": "row_number", "as": "_row"}]},
        {"filter": "datum._row == 1"},
    ]


def test_kpi_without_numbers_falls_back_to_table() -> None:
    spec = build_vega_spec("kpi", "temp_result_v1", data=[{"region": "msk"}])
    assert spec["usermeta"]["chart_type"] == "table"
    assert spec["usermeta"]["requested_type"] == "kpi"
    assert spec["mark"] == "text"


def test_kpi_without_title_is_captioned_by_its_metric() -> None:
    spec = build_vega_spec("kpi", None, data=[{"total": 5}], titles={"total": "Выручка"})
    assert spec["title"] == "Выручка"
    assert build_vega_spec("kpi", None, data=[{"total": 5}])["title"] == "total"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ([{"region": "msk", "revenue": 1.0}], "Выручка по региону"),
        ([{"region": "msk"}], f"{COUNT_TITLE} по региону"),
        ([{"revenue": 1.0}], "Выручка"),
        ([{"cnt": 1}], "cnt"),
        ([], None),
    ],
)
def test_chart_title_from_column_aliases(data: list[dict], expected: str | None) -> None:
    titles = {"region": "Региону", "revenue": "Выручка"}
    assert chart_title(data, titles) == expected


def test_ordered_column_prefers_dates_then_names() -> None:
    assert ordered_column({"region": "nominal", "month": "temporal"}) == "month"
    assert ordered_column({"region": "nominal", "year": "quantitative"}) == "year"
    assert ordered_column({"region": "nominal", "revenue": "quantitative"}) is None


def test_heatmap_overflow_and_corner_coverage() -> None:
    # Overflow/NaN in Decimal float conversion
    assert is_integer_value(Decimal("1e10000")) is False
    assert is_integer_value(Decimal("NaN")) is False
    # Non-standard field type for axis check
    assert is_heatmap_axis([{"x": 1}], {"x": "unknown_type"}, "x") is False
    # _temporal_step with unparseable/non-date types
    assert _temporal_step([123, object()]) == ("yearmonthdate", "%d.%m.%Y")
    # Heatmap where color was picked (existing column, but nominal, not quantitative),
    # so color channel falls back to count and color is removed from usermeta
    spec = build_vega_spec(
        "heatmap",
        data=[{"region": "msk", "segment": "b2b"}],
        color="region",
    )
    assert spec["usermeta"]["chart_type"] == "heatmap"
    assert "color" not in spec["usermeta"]

    # Heatmap where y is metric, color is None, but no replacements exist -> falls back to bar
    # and usermeta x, y are set to bar channels
    spec_bar = build_vega_spec(
        "heatmap",
        data=[{"region": "msk", "revenue": 10.5}],
        x="region",
        y="revenue",
        color=None,
    )
    assert spec_bar["usermeta"]["chart_type"] == "bar"
    assert spec_bar["usermeta"]["x"] == "region"
    assert spec_bar["usermeta"]["y"] == "revenue"


def test_heatmap_cohort_svg_renders_date_labels_not_epoch() -> None:
    cohort = [
        {"cohort_week": dt.date(2024, 1, 1), "week_number": 0, "retention": 1.0},
        {"cohort_week": dt.date(2024, 1, 8), "week_number": 0, "retention": 1.0},
        {"cohort_week": dt.date(2024, 1, 1), "week_number": 1, "retention": 0.65},
    ]
    spec = build_vega_spec("heatmap", data=cohort)
    svg = vlc.vegalite_to_svg(spec)
    labels = re.findall(r"<text[^>]*>(.*?)</text>", svg)
    assert "01.01.2024" in labels
    assert "08.01.2024" in labels
    assert not any("1704" in label for label in labels)


def test_heatmap_wide_correlation_matrix_without_index_column() -> None:
    records = [
        {"male": 1.0, "age": 0.14, "isAlive": -0.15},
        {"male": 0.14, "age": 1.0, "isAlive": -0.31},
        {"male": -0.15, "age": -0.31, "isAlive": 1.0},
    ]
    spec = build_vega_spec("heatmap", "corr_table", data=records, title="Корреляционная матрица")
    assert spec["usermeta"]["chart_type"] == "heatmap"
    assert spec["mark"] == "rect"
    assert spec["encoding"]["color"]["scale"]["scheme"] == "redblue"
    assert spec["encoding"]["color"]["scale"]["domain"] == [-1, 1]
    assert spec["encoding"]["x"]["scale"]["domain"] == ["male", "age", "isAlive"]
    assert spec["encoding"]["y"]["scale"]["domain"] == ["male", "age", "isAlive"]
    assert len(spec["data"]["values"]) == 9
    svg = vlc.vegalite_to_svg(spec)
    assert "male" in svg
    assert "isAlive" in svg


def test_heatmap_wide_correlation_matrix_with_feature_column() -> None:
    records = [
        {"feature": "Price", "Price": 1.0, "Area": 0.4},
        {"feature": "Area", "Price": 0.4, "Area": 1.0},
    ]
    spec = build_vega_spec("heatmap", data=records)
    assert spec["usermeta"]["chart_type"] == "heatmap"
    assert spec["mark"] == "rect"
    assert spec["encoding"]["color"]["title"] == "Корреляция"
    assert len(spec["data"]["values"]) == 4


def test_heatmap_wide_general_pivot_matrix() -> None:
    records = [
        {"segment": "b2b", "2021": 100.0, "2022": 120.0, "2023": 150.0},
        {"segment": "b2c", "2021": 80.0, "2022": 90.0, "2023": 110.0},
    ]
    spec = build_vega_spec("heatmap", data=records)
    assert spec["usermeta"]["chart_type"] == "heatmap"
    assert spec["encoding"]["color"]["scale"]["scheme"] == "blues"
    assert spec["encoding"]["color"]["title"] == "Значение"
    assert spec["encoding"]["x"]["scale"]["domain"] == ["2021", "2022", "2023"]
    assert spec["encoding"]["y"]["scale"]["domain"] == ["b2b", "b2c"]
    assert len(spec["data"]["values"]) == 6


def test_heatmap_rejects_unique_row_id_and_long_text() -> None:
    # 5 rows with unique "column_name" (primary key), 1 nominal "type", 1 float metric, 1 large int, 1 long text
    records = [
        {
            "column_name": f"col_{i}",
            "type": "num" if i % 2 == 0 else "txt",
            "fill_pct": 90.0 + i,
            "unique_cnt": 1500 + i,
            "description": "very long description of column values exceeding fifty characters easily "
            * 2,
        }
        for i in range(5)
    ]
    types = infer_field_types(records)
    assert is_heatmap_axis(records, types, "column_name") is False
    assert is_heatmap_axis(records, types, "description") is False
    assert is_heatmap_axis(records, types, "unique_cnt") is False
    assert is_heatmap_axis(records, types, "type") is True
    # Because only 1 axis candidate exists, falls back to bar chart
    spec = build_vega_spec("heatmap", data=records)
    assert spec["usermeta"]["chart_type"] == "bar"
    assert spec["mark"] == "bar"


def test_wide_matrix_helpers_corner_cases() -> None:
    assert detect_wide_matrix([], {}) is None
    assert detect_wide_matrix([{"a": 1}], {"a": "quantitative"}) is None
    assert (
        detect_wide_matrix([{"a": 1, "b": 2}], {"a": "quantitative", "b": "quantitative"}) is None
    )
    # 3 non-quant columns -> None
    assert (
        detect_wide_matrix(
            [{"a": "x", "b": "y", "c": "z", "v": 1, "w": 2}],
            {
                "a": "nominal",
                "b": "nominal",
                "c": "nominal",
                "v": "quantitative",
                "w": "quantitative",
            },
        )
        is None
    )
    # 1 non-quant, 2 order quant columns -> None
    assert (
        detect_wide_matrix(
            [{"label": "a", "year": 2021, "month": 5}, {"label": "b", "year": 2022, "month": 6}],
            {"label": "nominal", "year": "quantitative", "month": "quantitative"},
        )
        is None
    )
    # is_correlation_matrix corner cases
    assert is_correlation_matrix([{"a": 1.0}], ["a", "b"]) is False
    assert is_correlation_matrix([{"a": "invalid"}], ["a"]) is False
    assert is_correlation_matrix([{"a": 2.5}], ["a"]) is False
    assert is_correlation_matrix([{"a": Decimal("NaN")}], ["a"]) is False
    assert is_correlation_matrix([{"a": Decimal("1e10000")}], ["a"]) is False
    assert is_correlation_matrix([{"a": 0.0}], ["a"]) is False
    assert is_correlation_matrix([{"a": 1.0, "b": None}, {"a": None, "b": 1.0}], ["a", "b"]) is True
    # is_heatmap_axis with empty records
    assert is_heatmap_axis([], {"col": "nominal"}, "col") is True
    # is_heatmap_axis with empty column values
    assert is_heatmap_axis([{"empty": None}], {"empty": "nominal"}, "empty") is True
    # distinct > 50 on integer column (with duplicate rows so not a primary key)
    assert (
        is_heatmap_axis([{"large": i % 55} for i in range(110)], {"large": "quantitative"}, "large")
        is False
    )
    # non-order int with negative values or > 50
    assert (
        is_heatmap_axis(
            [{"code": -5}],
            {"code": "quantitative"},
            "code",
        )
        is False
    )
    assert (
        is_heatmap_axis(
            [{"code": 100}],
            {"code": "quantitative"},
            "code",
        )
        is False
    )


def test_heatmap_wide_matrix_explicit_invalid_xy_unpivots_not_bar() -> None:
    # When user/model mistakenly passes numeric metric columns as x and y on a wide correlation matrix,
    # it must NOT fall back to _bar, but instead unpivot the wide matrix into heatmap cells.
    records = [
        {"male": 1.0, "age": -0.14, "isAlive": -0.05},
        {"male": -0.14, "age": 1.0, "isAlive": 0.65},
        {"male": -0.05, "age": 0.65, "isAlive": 1.0},
    ]
    spec = build_vega_spec("heatmap", data=records, x="male", y="age")
    assert spec["usermeta"]["chart_type"] == "heatmap"
    assert spec["mark"] == "rect"
    assert len(spec["data"]["values"]) == 9
    assert spec["encoding"]["color"]["title"] == "Корреляция"
    assert spec["encoding"]["x"]["field"] == "x"
    assert spec["encoding"]["y"]["field"] == "y"
