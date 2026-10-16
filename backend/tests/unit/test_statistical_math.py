"""Mathematical reference tests for statistical research tools."""

import pytest

from app.core.data.duckdb_manager import DuckDBManager
from app.core.tools.registry import tool_registry


def execute_tool(db: DuckDBManager, tool: str, columns: list[str], table: str = "math_raw") -> dict:
    result_table = tool_registry.get(tool).run(db, table, columns)
    return db.to_records(result_table)[0]


@pytest.fixture
def math_db() -> DuckDBManager:
    db = DuckDBManager(":memory:")
    db.execute(
        """
        CREATE TABLE math_raw AS SELECT * FROM (
            VALUES
                (1.0, 2.0, 'a'),
                (2.0, 4.0, 'a'),
                (3.0, 6.0, 'b'),
                (4.0, 8.0, 'b')
        ) AS rows(x, y, group_name)
        """
    )
    yield db
    db.close()


def number(value: object) -> float:
    return float(value)


def test_pearson_returns_known_perfect_correlation(math_db: DuckDBManager) -> None:
    row = execute_tool(math_db, "pearson_corr", ["x", "y"])
    assert number(row["correlation"]) == pytest.approx(1.0)


def test_pearson_returns_known_inverse_correlation(math_db: DuckDBManager) -> None:
    math_db.execute(
        "CREATE TABLE inverse AS SELECT * FROM (VALUES (1.0, 4.0), (2.0, 3.0), "
        "(3.0, 2.0), (4.0, 1.0)) AS rows(x, y)"
    )
    row = execute_tool(math_db, "pearson_corr", ["x", "y"], "inverse")
    assert number(row["correlation"]) == pytest.approx(-1.0)


def test_spearman_uses_average_ranks_for_ties(math_db: DuckDBManager) -> None:
    math_db.execute(
        "CREATE TABLE ties AS SELECT * FROM (VALUES (1.0, 1.0), (1.0, 2.0), "
        "(2.0, 2.0), (3.0, 3.0)) AS rows(x, y)"
    )
    row = execute_tool(math_db, "spearman_corr", ["x", "y"], "ties")
    assert number(row["correlation"]) == pytest.approx(5 / 6)


def test_linear_regression_returns_known_coefficients(math_db: DuckDBManager) -> None:
    row = execute_tool(math_db, "linear_regression", ["y", "x"])
    assert number(row["slope"]) == pytest.approx(2.0)
    assert number(row["intercept"]) == pytest.approx(0.0)
    assert number(row["r_squared"]) == pytest.approx(1.0)


def test_anova_returns_known_f_statistic(math_db: DuckDBManager) -> None:
    row = execute_tool(math_db, "anova_one_way", ["x", "group_name"])
    assert number(row["statistic"]) == pytest.approx(8.0)


def test_independent_t_test_returns_known_statistic(math_db: DuckDBManager) -> None:
    row = execute_tool(math_db, "t_test_ind", ["x", "y"])
    assert number(row["statistic"]) == pytest.approx(-1.7320508075688772)


def test_paired_t_test_returns_known_statistic(math_db: DuckDBManager) -> None:
    row = execute_tool(math_db, "t_test_dep", ["x", "y"])
    assert number(row["statistic"]) == pytest.approx(-3.872983346207417)


def test_mann_whitney_returns_known_u_statistic(math_db: DuckDBManager) -> None:
    row = execute_tool(math_db, "mann_whitney_u", ["x", "y"])
    assert number(row["statistic"]) == pytest.approx(3.0)


def test_describe_stats_returns_known_sample_statistics(math_db: DuckDBManager) -> None:
    row = execute_tool(math_db, "describe_stats", ["x"])
    assert row["n"] == 4
    assert number(row["mean"]) == pytest.approx(2.5)
    assert number(row["std"]) == pytest.approx(1.2909944487358056)
    assert number(row["min"]) == pytest.approx(1.0)
    assert number(row["max"]) == pytest.approx(4.0)
    assert number(row["q1"]) == pytest.approx(1.75)
    assert number(row["q3"]) == pytest.approx(3.25)
    assert number(row["skew"]) == pytest.approx(0.0)
    assert number(row["kurtosis"]) == pytest.approx(-1.36)


def test_bootstrap_ci_is_deterministic_and_uses_percentiles(math_db: DuckDBManager) -> None:
    row = execute_tool(math_db, "bootstrap_ci", ["x"])
    assert number(row["mean"]) == pytest.approx(2.5)
    assert number(row["ci_low"]) == pytest.approx(1.5)
    assert number(row["ci_high"]) == pytest.approx(3.5)
