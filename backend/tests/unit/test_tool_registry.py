"""Unit tests for the tool registry contract."""

import inspect

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
    "ks_test",
    "shapiro_wilk",
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


def test_registry_describes_each_tool_once() -> None:
    names = [line.split(":", 1)[0] for line in tool_registry.describe().splitlines()]
    assert len(names) == len(set(names))
    assert "funnel_drop_off" in names
    assert tool_registry.get("funnel-drop-off") is tool_registry.get("funnel_drop_off")


def test_every_tool_accepts_parameters() -> None:
    for name, tool in tool_registry._tools.items():
        parameters = inspect.signature(tool.run).parameters.values()
        assert any(parameter.kind is parameter.VAR_KEYWORD for parameter in parameters), name


def test_sandbox_bridge_is_not_offered_until_the_sandbox_exists() -> None:
    assert "run_wasm_python" not in tool_registry._tools
    assert "run_wasm_python" not in tool_registry.describe()


def test_registry_get_unknown_tool_is_explicit() -> None:
    try:
        tool_registry.get("does_not_exist")
    except KeyError as exc:
        assert exc.args == ("does_not_exist",)
    else:
        raise AssertionError("Unknown tool lookup must raise KeyError")
