"""Unit-тесты реестра инструментов."""

import pytest

from app.core.tools.registry import tool_registry


def test_registry_has_core_tools():
    tools = tool_registry.describe()
    assert "aggregate" in tools
    assert "time_bucket" in tools
    assert "pearson_corr" in tools
    assert "forecast" in tools
    assert "seasonality" in tools


def test_duplicate_tools_are_callable_but_hidden_from_planner():
    tools = tool_registry.describe()
    for name in ("time_series", "correlation", "product_performance", "conversion_funnel"):
        assert tool_registry.get(name).name == name
        assert f"- {name} [" not in tools


def test_registry_get_known_tool():
    tool = tool_registry.get("aggregate")
    assert tool is not None
    assert tool.name == "aggregate"


def test_registry_get_unknown_tool_raises_keyerror():
    with pytest.raises(KeyError):
        tool_registry.get("nonexistent_tool")
