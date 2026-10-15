"""Unit-тесты автосборки Vega-Lite спецификаций из результата шага."""

import datetime as dt
import json
import uuid
from decimal import Decimal

import pytest

from app.core.data.duckdb_manager import DuckDBManager
from app.core.viz.vega_spec_builder import (
    CHART_TYPES,
    COUNT_TITLE,
    SPEC_ROW_LIMIT,
    VEGA_LITE_SCHEMA,
    build_vega_spec,
    column_aliases,
    field_ref,
    infer_field_type,
    infer_field_types,
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


@pytest.mark.parametrize("chart_type", CHART_TYPES)
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
        "aliases": {"region": ["регион", "область"], "empty": [], "odd": 5},
        "aliases_raw": "свободный текст",
    }
    assert column_aliases(passport) == {"amount": "Сумма", "region": "Регион"}
    assert column_aliases({"glossary": None}) == {}
    assert column_aliases({}) == {}


def test_titles_use_aliases() -> None:
    encoding = encoding_of("bar", SALES, titles=column_aliases({"glossary": {"region": "регион"}}))
    assert encoding["x"]["title"] == "Регион"
    assert encoding["y"]["title"] == "revenue"
