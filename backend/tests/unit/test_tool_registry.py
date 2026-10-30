"""Unit tests for the tool registry contract."""

import inspect

import pytest

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
    "ks_test",
    "shapiro_wilk",
    "forecast",
    "seasonality",
    "period_compare",
    "lag_correlation",
    "detect_anomalies",
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


def test_signature_marks_required_arguments() -> None:
    # Без пометки модель ставила group_by без by, и план отклонялся дважды.
    signature = tool_registry.get("group_by").signature()
    assert "by: column | list[column] (обязательный)" in signature
    assert "function: COUNT|COUNT_DISTINCT|SUM|AVG|MIN|MAX|MEDIAN|STDDEV = 'COUNT'" in signature
    assert "(обязательный) =" not in tool_registry.describe()


def test_registry_describes_each_tool_once() -> None:
    names = [line[2:].split(" ", 1)[0] for line in tool_registry.describe().splitlines()]
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


def test_run_python_is_visible_only_with_sandbox_and_flag(monkeypatch) -> None:
    from app.config import settings

    for enabled, installed, visible in [
        (True, True, True),
        (True, False, False),
        (False, True, False),
    ]:
        monkeypatch.setattr(settings, "codegen_enabled", enabled)
        monkeypatch.setattr("app.core.tools.codegen.sandbox_available", lambda v=installed: v)
        assert ("- run_python [" in tool_registry.describe()) is visible
        if visible:
            assert tool_registry.get("run_python").kind == "transform"
        else:
            with pytest.raises(KeyError):
                tool_registry.get("run_python")


def test_generated_tool_registration_is_reversible() -> None:
    from app.core.tools.generated import build_tool
    from app.models.generated_tool import GeneratedToolSpec

    before = set(tool_registry.names())
    spec = GeneratedToolSpec(
        name="unit_generated_tool",
        description="Generated in a unit test",
        parameters=[{"name": "column", "type": "column"}],
        sql="SELECT {column}, COUNT(*) AS cnt FROM {input} GROUP BY {column}",
        checks=["SELECT COUNT(*) > 0 AS ok FROM __tool_result"],
        sample_arguments=[{"name": "column", "value": "region"}],
    )
    tool_registry.register_generated(build_tool(spec), spec)
    assert "unit_generated_tool" in tool_registry.names()
    assert "unit_generated_tool" in tool_registry.describe()
    assert tool_registry.generated_specs()["unit_generated_tool"] == spec
    assert tool_registry.unregister_generated(["unit_generated_tool"]) == ["unit_generated_tool"]
    assert "unit_generated_tool" not in tool_registry.names()
    assert set(tool_registry.names()) == before


def test_generated_tool_cannot_shadow_builtin() -> None:
    from app.core.tools.generated import build_tool
    from app.models.generated_tool import GeneratedToolSpec

    spec = GeneratedToolSpec(
        name="aggregate",
        description="Collision",
        parameters=[{"name": "column", "type": "column"}],
        sql="SELECT {column}, COUNT(*) AS cnt FROM {input} GROUP BY {column}",
        checks=["SELECT COUNT(*) > 0 AS ok FROM __tool_result"],
        sample_arguments=[{"name": "column", "value": "region"}],
    )
    with pytest.raises(ValueError, match="already registered"):
        tool_registry.register_generated(build_tool(spec), spec)


def test_run_python_requires_a_detailed_purpose(codegen_available) -> None:
    tool = tool_registry.get("run_python")
    with pytest.raises(ValueError, match="at least 20"):
        tool.parse_args(arguments={"purpose": "медиана"})
    args = tool.parse_args(arguments={"purpose": "Медиана выручки по окну 7 дней"})
    assert args.output == "table"
