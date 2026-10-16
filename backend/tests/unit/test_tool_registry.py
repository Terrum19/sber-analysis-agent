"""Unit tests for the tool registry contract."""

from app.core.tools.registry import tool_registry

EXPECTED_TOOLS = {
    "select_columns",
    "filter_rows",
    "create_column",
    "rename_column",
    "join_tables",
    "left_join_tables",
    "group_by",
    "pivot",
    "time_bucket",
    "lag",
    "lead",
    "aggregate",
    "time_series",
    "correlation",
    "pearson_corr",
    "spearman_corr",
    "linear_regression",
    "anova_one_way",
    "t_test_ind",
    "t_test_dep",
    "mann_whitney_u",
    "describe_stats",
    "bootstrap_ci",
    "bootstrap_corr",
    "chi_square",
    "search_categorical_values",
    "inspect_temp_table",
    "run_wasm_python",
    "ks_test",
    "forecast",
    "seasonality",
    "funnel_steps",
    "funnel_drop_off",
    "cohort_analysis",
    "retention_matrix",
    "revenue_analysis",
    "product_performance",
    "conversion_funnel",
    "avg_order_value",
    "trend_analysis",
    "seasonal_decompose",
}


def test_registry_exposes_every_tool_to_llm() -> None:
    assert EXPECTED_TOOLS <= set(tool_registry._tools)
    description = tool_registry.describe()
    for name in EXPECTED_TOOLS:
        assert f"{name}:" in description


def test_registry_get_unknown_tool_is_explicit() -> None:
    try:
        tool_registry.get("does_not_exist")
    except KeyError as exc:
        assert exc.args == ("does_not_exist",)
    else:
        raise AssertionError("Unknown tool lookup must raise KeyError")
