"""Mathematical reference tests for statistical research tools."""

import pytest
from scipy import stats

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
    assert 0 <= number(row["p_value"]) <= 1


def test_independent_t_test_accepts_value_and_group_columns(math_db: DuckDBManager) -> None:
    math_db.execute(
        "CREATE TABLE grouped AS SELECT * FROM (VALUES "
        "(1.0, 'a'), (2.0, 'a'), (5.0, 'b'), (6.0, 'b')) AS rows(value, group_name)"
    )
    row = execute_tool(math_db, "t_test_ind", ["value", "group_name"], "grouped")
    assert number(row["statistic"]) < 0
    assert 0 <= number(row["p_value"]) <= 1


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
    assert number(row["median"]) == pytest.approx(2.5)
    assert number(row["std"]) == pytest.approx(1.2909944487358056)
    assert number(row["min"]) == pytest.approx(1.0)
    assert number(row["max"]) == pytest.approx(4.0)
    assert number(row["q1"]) == pytest.approx(1.75)
    assert number(row["q3"]) == pytest.approx(3.25)
    assert number(row["skew"]) == pytest.approx(0.0)
    assert number(row["kurtosis"]) == pytest.approx(-1.36)


def test_bootstrap_ci_is_deterministic_and_uses_percentiles(math_db: DuckDBManager) -> None:
    row = execute_tool(math_db, "bootstrap_ci", ["x"])
    assert row["statistic"] == "mean"
    assert number(row["estimate"]) == pytest.approx(2.5)
    assert number(row["ci_low"]) == pytest.approx(1.5)
    assert number(row["ci_high"]) == pytest.approx(3.5)


def test_chi_square_and_ks_test_return_reference_fields(math_db: DuckDBManager) -> None:
    math_db.execute(
        "CREATE TABLE categorical AS SELECT * FROM (VALUES "
        "('a', 'x'), ('a', 'x'), ('b', 'y'), ('b', 'y')) AS rows(first_value, second_value)"
    )
    chi = execute_tool(math_db, "chi_square", ["first_value", "second_value"], "categorical")
    assert number(chi["statistic"]) == pytest.approx(1.0)
    assert number(chi["p_value"]) == pytest.approx(0.31731050786291115)
    ks = execute_tool(math_db, "ks_test", ["x"])
    reference = stats.kstest([1.0, 2.0, 3.0, 4.0], stats.norm(2.5, 1.2909944487358056).cdf)
    assert number(ks["statistic"]) == pytest.approx(reference.statistic)
    assert number(ks["p_value"]) == pytest.approx(reference.pvalue)


def test_hypothesis_tests_report_scipy_p_values(math_db: DuckDBManager) -> None:
    first, second = [1.0, 2.0, 3.0, 4.0], [2.0, 4.0, 6.0, 8.0]
    anova = execute_tool(math_db, "anova_one_way", ["x", "group_name"])
    assert number(anova["p_value"]) == pytest.approx(stats.f_oneway([1.0, 2.0], [3.0, 4.0]).pvalue)
    assert (anova["df_between"], anova["df_within"]) == (1, 2)
    paired = execute_tool(math_db, "t_test_dep", ["x", "y"])
    assert number(paired["p_value"]) == pytest.approx(stats.ttest_rel(first, second).pvalue)
    assert number(paired["degrees_of_freedom"]) == pytest.approx(3)
    mann_whitney = execute_tool(math_db, "mann_whitney_u", ["x", "y"])
    assert number(mann_whitney["p_value"]) == pytest.approx(
        stats.mannwhitneyu(first, second).pvalue
    )
    welch = execute_tool(math_db, "t_test_ind", ["x", "y"])
    reference = stats.ttest_ind(first, second, equal_var=False)
    assert number(welch["p_value"]) == pytest.approx(reference.pvalue)
    assert number(welch["degrees_of_freedom"]) == pytest.approx(reference.df)
    regression = execute_tool(math_db, "linear_regression", ["y", "x"])
    assert number(regression["stderr"]) == pytest.approx(0.0)


def test_independent_t_test_treats_binary_numeric_column_as_groups(
    math_db: DuckDBManager,
) -> None:
    math_db.execute(
        "CREATE TABLE binary_groups AS SELECT * FROM (VALUES (10.0, 0), (12.0, 0), (11.0, 0), "
        "(30.0, 1), (33.0, 1), (31.0, 1)) AS rows(revenue, grp)"
    )
    row = execute_tool(math_db, "t_test_ind", ["revenue", "grp"], "binary_groups")
    reference = stats.ttest_ind([10.0, 12.0, 11.0], [30.0, 33.0, 31.0], equal_var=False)
    assert number(row["statistic"]) == pytest.approx(reference.statistic)
    assert number(row["p_value"]) == pytest.approx(reference.pvalue)
    samples = tool_registry.get("t_test_ind").run(
        math_db, "binary_groups", ["revenue", "grp"], mode="samples"
    )
    labels = stats.ttest_ind(
        [10.0, 12.0, 11.0, 30.0, 33.0, 31.0], [0.0, 0.0, 0.0, 1.0, 1.0, 1.0], equal_var=False
    )
    assert number(math_db.to_records(samples)[0]["statistic"]) == pytest.approx(labels.statistic)


def test_non_finite_statistics_are_stored_as_double(math_db: DuckDBManager) -> None:
    math_db.execute(
        "CREATE TABLE flat AS SELECT * FROM (VALUES (1.0, 'a'), (1.0, 'a'), "
        "(2.0, 'b'), (2.0, 'b')) AS rows(value, group_name)"
    )
    row = execute_tool(math_db, "t_test_ind", ["value", "group_name"], "flat")
    assert row["statistic"] == float("-inf")
    assert isinstance(row["p_value"], float)


def test_shapiro_wilk_matches_scipy_and_limits_sample_size(math_db: DuckDBManager) -> None:
    row = execute_tool(math_db, "shapiro_wilk", ["x"])
    reference = stats.shapiro([1.0, 2.0, 3.0, 4.0])
    assert number(row["statistic"]) == pytest.approx(reference.statistic)
    assert number(row["p_value"]) == pytest.approx(reference.pvalue)
    math_db.execute("CREATE TABLE large AS SELECT range::DOUBLE AS x FROM range(5001)")
    with pytest.raises(ValueError, match="at most 5000"):
        execute_tool(math_db, "shapiro_wilk", ["x"], "large")


def test_two_sample_tests_accept_an_alternative_hypothesis(math_db: DuckDBManager) -> None:
    first, second = [1.0, 2.0, 3.0, 4.0], [2.0, 4.0, 6.0, 8.0]
    for name, reference in [
        ("t_test_ind", stats.ttest_ind(first, second, equal_var=False, alternative="less")),
        ("t_test_dep", stats.ttest_rel(first, second, alternative="less")),
        ("mann_whitney_u", stats.mannwhitneyu(first, second, alternative="less")),
    ]:
        table = tool_registry.get(name).run(math_db, "math_raw", ["x", "y"], alternative="less")
        assert number(math_db.to_records(table)[0]["p_value"]) == pytest.approx(reference.pvalue)
    with pytest.raises(ValueError, match="alternative"):
        tool_registry.get("t_test_ind").run(math_db, "math_raw", ["x", "y"], alternative="up")
