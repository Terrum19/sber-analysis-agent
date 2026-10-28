"""Типизированные схемы тулов: oneOf/enum и аргументы."""

from app.core.tools.registry import tool_registry
from app.core.tools.schema import compact_planner_schema, planner_schema, tool_schema


def test_tool_schema_contains_argument_model() -> None:
    schema = tool_schema(tool_registry.get("group_by"))
    assert schema.name == "group_by"
    assert "by" in schema.json_schema["properties"]
    assert "function" in schema.json_schema["properties"]


def test_planner_schema_uses_oneof_for_topk() -> None:
    tools = [tool_registry.get("group_by"), tool_registry.get("aggregate")]
    schema = planner_schema(tools, max_steps=4)
    steps = schema["properties"]["steps"]
    assert steps["maxItems"] == 4
    assert len(steps["items"]["oneOf"]) == 2
    names = {variant["properties"]["tool"]["const"] for variant in steps["items"]["oneOf"]}
    assert names == {"group_by", "aggregate"}


def test_compact_schema_uses_enum() -> None:
    tools = tool_registry.planner_tools()
    schema = compact_planner_schema(tools, max_steps=8)
    enum = schema["properties"]["steps"]["items"]["properties"]["tool"]["enum"]
    assert "group_by" in enum
    assert len(enum) == len(tools)
