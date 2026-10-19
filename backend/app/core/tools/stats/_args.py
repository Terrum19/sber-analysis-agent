"""Общие модели аргументов стат-тулов."""

from typing import Literal

from app.core.tools.base import Ident, ToolArgs


class OneColumnArgs(ToolArgs):
    column: Ident


class TwoColumnsArgs(ToolArgs):
    column_a: Ident
    column_b: Ident


class TwoSamplesArgs(ToolArgs):
    """column_b — вторая числовая колонка или колонка с двумя группами."""

    column_a: Ident
    column_b: Ident
    mode: Literal["auto", "columns", "groups"] = "auto"
