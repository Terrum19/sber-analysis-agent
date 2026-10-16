"""Unit-тесты реестра инструментов."""

import pytest

from app.core.tools.registry import tool_registry


def test_registry_has_five_tools():
    tools = tool_registry.describe()
    assert "aggregate" in tools
    assert "time_series" in tools
    assert "correlation" in tools
    assert "forecast" in tools
    assert "seasonality" in tools


def test_registry_get_known_tool():
    tool = tool_registry.get("aggregate")
    assert tool is not None
    assert tool.name == "aggregate"


def test_registry_get_unknown_tool_raises_keyerror():
    with pytest.raises(KeyError):
        tool_registry.get("nonexistent_tool")
