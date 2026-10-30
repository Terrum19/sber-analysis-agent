"""Dry-run проверка плана на EXPLAIN (P1)."""

from __future__ import annotations

import logging

import duckdb

from app.core.agent.step_executor import tool_kind
from app.core.tools.registry import tool_registry
from app.models.research_step import PREVIOUS, SOURCE, ResearchPlan

logger = logging.getLogger(__name__)


class DryRunValidator:
    """Проверяет SQL шагов через ``EXPLAIN`` до выполнения."""

    def __init__(self, db) -> None:
        self.db = db

    def validate(self, plan: ResearchPlan, source_table: str) -> list[str]:
        """Ошибки SQL-шагов над источником; шаги над $prev/$step_N пропускаются."""
        errors: list[str] = []
        for step in plan.steps:
            if step.input not in (SOURCE, PREVIOUS):
                continue
            if step.input == PREVIOUS and any(
                tool_kind(other) == "transform" for other in plan.steps[: plan.steps.index(step)]
            ):
                continue
            try:
                tool = tool_registry.get(step.tool or "")
                args = tool.parse_args(step.columns, step.arguments)
                sql = tool.build_sql(self.db, source_table, args)
                if sql is None:
                    continue
                self.explain(sql)
            except (KeyError, ValueError):
                # Синтаксис аргументов уже проверяет validate_plan.
                continue
            except duckdb.Error as exc:
                errors.append(f"шаг {step.step_id}: {exc}")
        return errors

    def explain(self, sql: str) -> list[dict]:
        """EXPLAIN без выполнения запроса."""
        return self.db.query(f"EXPLAIN {sql}")

    def validate_sql(self, sql: str) -> list[str]:
        """Проверить один SQL (например, semantic compiler.to_sql)."""
        try:
            self.explain(sql)
        except duckdb.Error as exc:
            return [str(exc)]
        return []
