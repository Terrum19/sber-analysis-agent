"""Unit tests for the tool registry contract."""

from app.core.tools.registry import tool_registry

HIDDEN_TOOLS = {"time_series", "correlation", "product_performance", "conversion_funnel"}

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
    "sort_limit",
    "sample",
    "window_metric",
}


def test_registry_exposes_every_tool_to_llm() -> None:
    assert EXPECTED_TOOLS <= set(tool_registry._tools)
    description = tool_registry.describe()
    for name in EXPECTED_TOOLS - HIDDEN_TOOLS:
        assert f"- {name} [" in description
    for name in HIDDEN_TOOLS:
        assert f"- {name} [" not in description


def test_every_planner_tool_describes_its_arguments() -> None:
    for tool in tool_registry.planner_tools():
        assert "arguments={" in tool.describe()
        assert tool.kind in {"transform", "analysis", "lookup"}


def test_registry_get_unknown_tool_is_explicit() -> None:
    try:
        tool_registry.get("does_not_exist")
    except KeyError as exc:
        assert exc.args == ("does_not_exist",)
    else:
        raise AssertionError("Unknown tool lookup must raise KeyError")
