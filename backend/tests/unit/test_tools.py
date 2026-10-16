"""Unit tests for research tools and their DuckDB contracts."""

import pytest

from app.core.data.duckdb_manager import DuckDBManager
from app.core.tools.registry import tool_registry


@pytest.fixture
def db() -> DuckDBManager:
    manager = DuckDBManager(":memory:")
    manager.execute(
        """
        CREATE TABLE raw AS SELECT * FROM (
            VALUES
                (1, DATE '2024-01-01', 'a', 10.0, 2.0),
                (2, DATE '2024-02-01', 'b', 20.0, 4.0),
                (3, DATE '2024-03-01', 'a', 30.0, 8.0),
                (4, DATE '2024-04-01', 'b', 40.0, 10.0)
        ) AS rows(id, event_date, category, amount, other_amount)
        """
    )
    manager.execute(
        """
        CREATE TABLE right_table AS SELECT * FROM (
            VALUES (1, 'first'), (3, 'third')
        ) AS rows(id, label)
        """
    )
    yield manager
    manager.close()


def run_tool(db: DuckDBManager, name: str, columns: list[str]) -> tuple[str, list[dict]]:
    table = tool_registry.get(name).run(db, "raw", columns)
    assert table.startswith("temp_result_v")
    return table, db.to_records(table)


@pytest.mark.parametrize(
    ("name", "columns"),
    [
        ("select_columns", ["id", "amount"]),
        ("filter_rows", ["amount", "20", ">="]),
        ("create_column", ["total", "amount + other_amount"]),
        ("rename_column", ["amount", "value"]),
        ("join_tables", ["right_table", "t.id = t_right.id"]),
        ("left_join_tables", ["right_table", "t.id = t_right.id"]),
        ("group_by", ["category", "amount", "SUM"]),
        ("pivot", ["amount", "category", "id"]),
        ("time_bucket", ["event_date", "month"]),
        ("lag", ["amount", "1"]),
        ("lead", ["amount", "1"]),
        ("aggregate", ["category"]),
        ("time_series", ["event_date", "amount"]),
        ("correlation", ["amount", "other_amount"]),
    ],
)
def test_sql_tools_return_cached_temp_tables(
    db: DuckDBManager, name: str, columns: list[str]
) -> None:
    table, records = run_tool(db, name, columns)
    assert db.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"] == len(records)


def test_atomic_tools_preserve_expected_values(db: DuckDBManager) -> None:
    _, records = run_tool(db, "create_column", ["total", "amount + other_amount"])
    assert records[0]["total"] == 12

    _, records = run_tool(db, "filter_rows", ["category", "a", "="])
    assert [row["id"] for row in records] == [1, 3]

    _, records = run_tool(db, "left_join_tables", ["right_table", "t.id = t_right.id"])
    assert next(row for row in records if row["id"] == 2)["label"] is None


@pytest.mark.parametrize(
    ("name", "columns", "expected_columns"),
    [
        ("pearson_corr", ["amount", "other_amount"], {"correlation"}),
        ("spearman_corr", ["amount", "other_amount"], {"correlation"}),
        ("linear_regression", ["amount", "other_amount"], {"slope", "intercept", "r_squared"}),
        ("anova_one_way", ["amount", "category"], {"statistic"}),
        ("t_test_ind", ["amount", "other_amount"], {"statistic"}),
        ("t_test_dep", ["amount", "other_amount"], {"statistic"}),
        ("mann_whitney_u", ["amount", "other_amount"], {"statistic"}),
        (
            "describe_stats",
            ["amount"],
            {"n", "mean", "std", "min", "max", "q1", "q3", "skew", "kurtosis"},
        ),
        ("bootstrap_ci", ["amount"], {"mean", "ci_low", "ci_high"}),
        ("search_categorical_values", ["category", "a"], {"category"}),
    ],
)
def test_statistical_tools_return_results(
    db: DuckDBManager, name: str, columns: list[str], expected_columns: set[str]
) -> None:
    table, records = run_tool(db, name, columns)
    assert records
    assert expected_columns == {
        row["column_name"] for row in db.query(f"DESCRIBE {table}")
    }


def test_time_series_and_seasonality_are_aggregations(db: DuckDBManager) -> None:
    _, time_series = run_tool(db, "time_series", ["event_date", "amount"])
    assert [row["metric_avg"] for row in time_series] == [10.0, 20.0, 30.0, 40.0]

    _, seasonality = run_tool(db, "seasonality", ["event_date", "amount"])
    assert seasonality[0]["month"] == 1
    assert seasonality[0]["avg_level"] == 10.0


def test_forecast_returns_requested_number_of_periods(db: DuckDBManager) -> None:
    table, records = run_tool(db, "forecast", ["event_date", "amount", "2"])
    assert len(records) == 2
    assert set(db.query(f"DESCRIBE {table}")[0]) >= {"column_name"}


@pytest.mark.parametrize(
    ("name", "columns"),
    [
        ("select_columns", []),
        ("filter_rows", ["amount"]),
        ("create_column", ["new"]),
        ("group_by", ["category", "amount", "MEDIAN"]),
        ("time_bucket", ["event_date", "year"]),
        ("lag", ["amount", "-1"]),
        ("pearson_corr", ["amount"]),
        ("bootstrap_ci", ["amount;"]),
        ("search_categorical_values", ["category"]),
    ],
)
def test_tools_reject_invalid_arguments(
    db: DuckDBManager, name: str, columns: list[str]
) -> None:
    with pytest.raises(ValueError):
        tool_registry.get(name).run(db, "raw", columns)


def test_registry_is_extensible_without_orchestrator_changes(db: DuckDBManager) -> None:
    class MarkerTool:
        name = "marker"
        description = "marker"

        def run(self, db, input_table, columns):
            return db.save_step_result(input_table, "SELECT 1 AS marker")

        def describe(self):
            return f"{self.name}: {self.description}"

    tool_registry.register(MarkerTool())
    try:
        assert tool_registry.get("marker").run(db, "raw", []) == "temp_result_v1"
        assert "marker: marker" in tool_registry.describe()
    finally:
        tool_registry._tools.pop("marker", None)
