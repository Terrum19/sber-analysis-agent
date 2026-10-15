import json
from datetime import date

import pytest

from app.config import settings
from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.profiling import (
    TableProfile,
    classify,
    format_quality,
    outlier_summary,
    profile_table,
    quality_notes,
    quote_table,
    schema_overview,
)
from app.core.llm.prompts.schema_describe import SCHEMA_DESCRIBE


class CountingDB:
    def __init__(self, db: DuckDBManager) -> None:
        self.db = db
        self.queries: list[str] = []

    def query(self, sql: str, params: dict | None = None) -> list[dict]:
        self.queries.append(sql)
        return self.db.query(sql, params)


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    yield manager
    manager.close()


def make_table(db: DuckDBManager, name: str, columns: str, rows: list[tuple]) -> None:
    db.execute(f"CREATE TABLE {name} ({columns})")
    for row in rows:
        placeholders = ", ".join("?" * len(row))
        db._con.execute(f"INSERT INTO {name} VALUES ({placeholders})", list(row))


def test_known_distribution(db) -> None:
    make_table(db, "numbers", "value INTEGER", [(1,), (2,), (2,), (3,), (100,)])
    profile = profile_table(db, "numbers", approx=False)
    column = profile.column("value")
    assert profile.row_count == 5
    assert profile.column_count == 1
    assert column.kind == "numeric"
    assert column.skewness > 0
    assert column.kurtosis > 0
    assert column.q1 == 2
    assert column.q3 == 3
    assert column.iqr == 1
    assert column.median == 2
    assert column.mode == 2
    assert column.min == 1
    assert column.max == 100
    assert column.mean == pytest.approx(21.6)
    assert column.std == pytest.approx(39.2051017)
    assert column.p05 == pytest.approx(1.2)
    assert column.p95 == pytest.approx(80.6)
    assert column.distinct == 4
    assert column.distinct_pct == 80.0
    assert column.top_values[0] == {"value": 2, "count": 2}
    assert len(column.top_values) == 4
    assert column.outlier_count == 1
    assert column.nan_pct is None
    assert column.null_pct == 0.0
    assert not column.is_constant
    assert not column.is_unique


def test_nan_and_null_percentages(db) -> None:
    make_table(
        db,
        "measures",
        "reading DOUBLE, label VARCHAR",
        [(1.0, "a"), (float("nan"), None), (None, "b"), (3.0, "c")],
    )
    profile = profile_table(db, "measures", approx=False)
    reading = profile.column("reading")
    label = profile.column("label")
    assert reading.nan_pct == 25.0
    assert reading.null_pct == 25.0
    assert reading.max == 3.0
    assert reading.mean == 2.0
    assert reading.distinct == 2
    assert reading.outlier_count == 0
    assert label.null_pct == 25.0
    assert label.nan_pct is None


def test_text_dates_and_flags(db) -> None:
    make_table(
        db,
        "people",
        "name VARCHAR, born DATE, team VARCHAR, active BOOLEAN, tags INTEGER[]",
        [
            ("Ann", date(1990, 5, 1), "core", True, [1]),
            ("Bartholomew", date(1985, 1, 31), "core", True, [2]),
            ("Cy", date(2001, 12, 24), "core", False, None),
        ],
    )
    profile = profile_table(db, "people")
    name = profile.column("name")
    born = profile.column("born")
    team = profile.column("team")
    active = profile.column("active")
    tags = profile.column("tags")
    assert name.min_len == 2
    assert name.max_len == 11
    assert name.is_unique
    assert name.mode is None
    assert born.kind == "temporal"
    assert born.min_date == date(1985, 1, 31)
    assert born.max_date == date(2001, 12, 24)
    assert team.is_constant
    assert team.mode == "core"
    assert active.kind == "bool"
    assert active.top_values == [{"value": True, "count": 2}, {"value": False, "count": 1}]
    assert tags.kind == "other"
    assert tags.nulls == 1
    assert tags.distinct is None
    assert tags.top_values == []


def test_empty_table(db) -> None:
    db.execute("CREATE TABLE nothing (value DOUBLE, name VARCHAR, day DATE)")
    profile = profile_table(db, "nothing")
    assert profile.row_count == 0
    assert profile.column_count == 3
    for column in profile.columns:
        assert column.total == 0
        assert column.null_pct is None
        assert column.distinct is None
        assert column.mean is None
        assert column.q1 is None
        assert not column.is_constant
        assert not column.is_unique
    assert quality_notes(profile) == ["таблица пуста"]
    json.dumps(profile.to_dict())


def test_single_row(db) -> None:
    make_table(db, "single", "value DOUBLE, name VARCHAR", [(4.5, "x")])
    profile = profile_table(db, "single", approx=False)
    value = profile.column("value")
    assert value.mean == 4.5
    assert value.std == 0.0
    assert value.skewness is None
    assert value.kurtosis is None
    assert value.iqr == 0.0
    assert value.is_constant
    assert value.is_unique
    assert value.mode == 4.5
    assert quality_notes(profile) == []


def test_constant_and_all_null_columns(db) -> None:
    make_table(
        db,
        "flat",
        "value INTEGER, nothing DOUBLE",
        [(7, None), (7, None), (7, None)],
    )
    profile = profile_table(db, "flat", approx=False)
    value = profile.column("value")
    nothing = profile.column("nothing")
    assert value.is_constant
    assert value.std == 0.0
    assert value.skewness is None
    assert value.outlier_count == 0
    assert nothing.null_pct == 100.0
    assert nothing.mean is None
    assert nothing.distinct == 0
    assert nothing.top_values == []
    assert nothing.outlier_count == 0
    assert not nothing.is_constant
    notes = quality_notes(profile)
    assert "«value»: константа (7)" in notes
    assert "«nothing»: все значения пустые" in notes


def test_all_unique_text_is_flagged(db) -> None:
    make_table(db, "ids", "code VARCHAR", [("a1",), ("b2",), ("c3",)])
    profile = profile_table(db, "ids")
    assert profile.column("code").is_unique
    assert quality_notes(profile) == ["«code»: все значения уникальны — похоже на идентификатор"]


def test_query_budget_for_ten_columns(db) -> None:
    columns = (
        "a INTEGER, b DOUBLE, c DECIMAL(10, 2), d VARCHAR, e VARCHAR, "
        "f DATE, g TIMESTAMP, h BOOLEAN, i BIGINT, j FLOAT"
    )
    db.execute(f"CREATE TABLE wide ({columns})")
    db.execute(
        "INSERT INTO wide SELECT range, range / 3, range, 'x' || range, 'y', "
        "DATE '2024-01-01' + range::INTEGER, TIMESTAMP '2024-01-01' + range * INTERVAL 1 HOUR, "
        "range % 2 = 0, range * 10, range FROM range(50)"
    )
    counting = CountingDB(db)
    profile = profile_table(counting, "wide")
    assert len(counting.queries) <= 4
    assert profile.column_count == 10
    assert profile.row_count == 50


def test_approx_quantiles_for_large_tables(db, monkeypatch) -> None:
    monkeypatch.setattr(settings, "profile_approx_min_rows", 10)
    db.execute("CREATE TABLE big AS SELECT range::DOUBLE AS value FROM range(1000)")
    counting = CountingDB(db)
    profile = profile_table(counting, "big")
    assert any("approx_quantile" in sql for sql in counting.queries)
    assert profile.column("value").q1 == pytest.approx(249.75, abs=5)
    counting = CountingDB(db)
    profile_table(counting, "big", approx=False)
    assert not any("approx_quantile" in sql for sql in counting.queries)
    assert len(counting.queries) == 3


def test_top_values_limit(db) -> None:
    db.execute("CREATE TABLE many AS SELECT range % 7 AS value FROM range(70)")
    profile = profile_table(db, "many", top_values_limit=3)
    column = profile.column("value")
    assert len(column.top_values) == 3
    assert column.distinct == 7
    assert column.mode == 0


def test_quoted_names_and_serialization(db) -> None:
    db.execute('CREATE TABLE "odd ""table""" ("a b" INTEGER, "when" TIMESTAMP, token UUID)')
    db.execute(
        'INSERT INTO "odd ""table""" VALUES (1, TIMESTAMP \'2024-03-01 10:00:00\', '
        "'00000000-0000-0000-0000-000000000001')"
    )
    profile = profile_table(db, 'odd "table"')
    assert profile.column("a b").mean == 1.0
    assert profile.column("missing") is None
    data = profile.to_dict()
    assert data["columns"][1]["min_date"] == "2024-03-01T10:00:00"
    assert data["row_count"] == 1
    assert data["columns"][2]["mode"] == "00000000-0000-0000-0000-000000000001"
    json.dumps(data)


def test_table_without_columns() -> None:
    class EmptySchema:
        def query(self, sql: str) -> list[dict]:
            return []

    profile = profile_table(EmptySchema(), "ghost")
    assert profile.column_count == 0
    assert profile.columns == []


def test_quote_table_splits_schema() -> None:
    assert quote_table("main.events") == '"main"."events"'


@pytest.mark.parametrize(
    ("dtype", "kind"),
    [
        ("INTEGER", "numeric"),
        ("DECIMAL(18,3)", "numeric"),
        ("DOUBLE", "numeric"),
        ("TIMESTAMP WITH TIME ZONE", "temporal"),
        ("VARCHAR", "text"),
        ("ENUM('a', 'b')", "text"),
        ("UUID", "text"),
        ("BOOLEAN", "bool"),
        ("TIME", "other"),
        ("STRUCT(x INTEGER)", "other"),
    ],
)
def test_classify(dtype: str, kind: str) -> None:
    assert classify(dtype) == kind


def test_reports(db) -> None:
    make_table(
        db,
        "orders",
        "amount DOUBLE, region VARCHAR",
        [
            (1.0, "msk"),
            (2.0, None),
            (2.0, "spb"),
            (3.0, "msk"),
            (100.0, "msk"),
            (float("nan"), "spb"),
        ],
    )
    profile = profile_table(db, "orders", approx=False)
    overview = schema_overview(profile)
    assert "- amount: DOUBLE (numeric), пропусков 0%" in overview
    assert "region: VARCHAR (text), пропусков 16.6667%" in overview
    quality = format_quality(profile)
    assert "«amount»: NaN 16.6667%" in quality
    assert "«amount»: выбросов за 1.5·IQR — 1" in quality
    assert "«region»: пропусков 16.6667%" in quality
    summary = outlier_summary(profile)
    assert summary == {
        "amount": {"method": "iqr_1.5", "lower_fence": 0.5, "upper_fence": 4.5, "count": 1}
    }


def test_reports_without_notes() -> None:
    profile = TableProfile(table="t", row_count=2, column_count=0, columns=[], profiled_at="")
    assert format_quality(profile) == "- замечаний нет"
    assert schema_overview(profile) == ""


def test_schema_describe_prompt_has_quality_block(db) -> None:
    make_table(db, "gaps", "value DOUBLE", [(1.0,), (None,)])
    profile = profile_table(db, "gaps")
    prompt = SCHEMA_DESCRIBE.format(profile=profile.to_dict(), quality=format_quality(profile))
    assert "Качество данных:\n- «value»: пропусков 50%" in prompt
