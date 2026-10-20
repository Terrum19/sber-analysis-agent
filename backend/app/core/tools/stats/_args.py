"""Общие модели аргументов стат-тулов."""

from typing import Literal

from pydantic import model_validator

from app.core.tools.base import Ident, ToolArgs

Alternative = Literal["two-sided", "less", "greater"]


class OneColumnArgs(ToolArgs):
    column: Ident


class TwoColumnsArgs(ToolArgs):
    column_a: Ident
    column_b: Ident


class TwoSamplesArgs(ToolArgs):
    """column_b — вторая числовая колонка (mode=samples) или колонка с двумя группами
    (mode=groups); auto — groups для текстовой колонки или числовой ровно с двумя значениями."""

    column_a: Ident
    column_b: Ident
    mode: Literal["auto", "samples", "groups"] = "auto"
    alternative: Alternative = "two-sided"

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: dict) -> dict:
        # "columns" — прежнее имя режима samples в планах planner.
        if isinstance(data, dict) and isinstance(data.get("mode"), str):
            mode = data["mode"].lower()
            data["mode"] = "samples" if mode == "columns" else mode
        if isinstance(data, dict) and isinstance(data.get("alternative"), str):
            data["alternative"] = data["alternative"].lower()
        return data
