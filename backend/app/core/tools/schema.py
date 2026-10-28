"""Типизированные JSON-схемы тулов для планировщика (P1).

Для top-k тулов строится ``oneOf``: аргументы каждого шага проверяет
грамматика сервера. Для полного списка (40 тулов) — компактная схема с
``tool.enum`` и ``arguments: object``, чтобы не раздувать промпт.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.core.tools.base import BaseTool


class ToolArgumentSchema(BaseModel):
    """Аргументы одного шага (для внешних потребителей схемы)."""

    tool: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolSchema:
    name: str
    description: str
    args_model: type[BaseModel]
    json_schema: dict[str, Any]


def tool_schema(tool: BaseTool) -> ToolSchema:
    """Pydantic-схема аргументов тула + JSON schema."""
    schema = tool.Args.model_json_schema()
    schema.pop("title", None)
    return ToolSchema(
        name=tool.name,
        description=tool.description,
        args_model=tool.Args,
        json_schema=schema,
    )


def planner_schema(tools: list[BaseTool], max_steps: int) -> dict[str, Any]:
    """JSON schema ResearchPlan: oneOf по тулам для top-k, иначе компактная."""
    base = {
        "type": "object",
        "properties": {
            "step_id": {"type": "integer"},
            "input": {"type": "string"},
            "purpose": {"type": "string"},
            "depends_on": {"type": "array", "items": {"type": "integer"}},
        },
        "required": ["tool"],
    }
    variants: list[dict[str, Any]] = []
    for tool in tools:
        arguments = tool_schema(tool).json_schema
        variant = {
            "type": "object",
            "properties": {
                **base["properties"],
                "tool": {"const": tool.name},
                "arguments": arguments,
            },
            "required": ["tool"],
        }
        variants.append(variant)
    steps: dict[str, Any] = {
        "type": "array",
        "maxItems": max_steps,
        "items": {"oneOf": variants} if variants else {"type": "object"},
    }
    return {
        "type": "object",
        "properties": {
            "steps": steps,
            "final_viz": {"type": "string"},
            "dashboard_requested": {"type": "boolean"},
            "result_step": {"type": ["integer", "null"]},
            "dashboard_title": {"type": ["string", "null"]},
            "template_id": {"type": ["string", "null"]},
            "dashboard_tiles": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["steps"],
    }


def compact_planner_schema(tools: list[BaseTool], max_steps: int) -> dict[str, Any]:
    """Компактная схема для полного списка тулов: enum имён + свободные аргументы."""
    return {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "maxItems": max_steps,
                "items": {
                    "type": "object",
                    "properties": {
                        "step_id": {"type": "integer"},
                        "tool": {"type": "string", "enum": [tool.name for tool in tools]},
                        "input": {"type": "string"},
                        "arguments": {"type": "object"},
                        "purpose": {"type": "string"},
                        "depends_on": {"type": "array", "items": {"type": "integer"}},
                    },
                    "required": ["tool"],
                },
            },
            "final_viz": {"type": "string"},
            "dashboard_requested": {"type": "boolean"},
            "result_step": {"type": ["integer", "null"]},
            "dashboard_title": {"type": ["string", "null"]},
            "template_id": {"type": ["string", "null"]},
            "dashboard_tiles": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["steps"],
    }
