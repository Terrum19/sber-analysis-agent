"""Параллельный исполнитель DAG-плана (P1, за флагом AGENT_DAG_EXECUTOR)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from app.config import settings
from app.core.agent.plan_graph import PlanGraph
from app.core.agent.step_executor import StepExecutor, tool_kind
from app.models.research_step import PREVIOUS, SOURCE, ResearchPlan, ResearchStep

logger = logging.getLogger(__name__)
Emit = Callable[[dict], None]


def _ignore(event: dict) -> None:
    pass


class ParallelPlanExecutor:
    """Выполняет слои DAG параллельно; ошибка узла не запускает dependents."""

    def __init__(self, executor: StepExecutor) -> None:
        self.executor = executor

    async def execute(
        self,
        graph: PlanGraph,
        input_table: str,
        source_table: str,
        emit: Emit = _ignore,
        plan: ResearchPlan | None = None,
    ) -> tuple[Any, dict[int, str]]:
        """Вернуть Execution-совместимый результат и таблицы шагов."""
        if plan is None:
            plan = ResearchPlan(steps=[graph.nodes[step_id].step for step_id in graph.order])
        errors = graph.validate()
        if errors:
            raise ValueError("; ".join(errors))
        tables: dict[int, str] = {}
        failed: set[int] = set()
        semaphore = asyncio.Semaphore(settings.agent_max_parallel_steps)
        last_transform = input_table

        async def run_node(step: ResearchStep) -> tuple[int, str | None, Exception | None]:
            nonlocal last_transform
            if any(dependency in failed for dependency in graph.nodes[step.step_id].depends_on):
                failed.add(step.step_id)
                return step.step_id, None, RuntimeError("зависимый шаг не выполнен")
            async with semaphore:
                emit(_progress(step, "started"))
                target = _input_for(step, graph, tables, last_transform, source_table)
                try:
                    table = await self.executor.execute(
                        step, target, step_tables=tables, source_table=source_table
                    )
                except Exception as error:  # noqa: BLE001 — изолируем узел
                    failed.add(step.step_id)
                    emit(_progress(step, "failed", error=str(error)))
                    logger.error("DAG: шаг %d упал: %s", step.step_id, error)
                    return step.step_id, None, error
                tables[step.step_id] = table
                if tool_kind(step) == "transform":
                    last_transform = table
                emit(_progress(step, "finished"))
                return step.step_id, table, None

        for layer in graph.layers():
            await asyncio.gather(*(run_node(step) for step in layer))

        from app.core.agent.orchestrator import Execution

        executed = [graph.nodes[step_id].step for step_id in graph.order if step_id in tables]
        if plan.result_step is not None and plan.result_step in tables:
            result_table = tables[plan.result_step]
        elif executed:
            result_table = tables[executed[-1].step_id]
        else:
            result_table = last_transform
        shown = next((step for step in executed if tables[step.step_id] == result_table), None)
        supporting = [
            (step.purpose or step.tool or f"шаг {step.step_id}", tables[step.step_id])
            for step in executed
            if tool_kind(step) == "analysis" and tables[step.step_id] != result_table
        ]
        return (
            Execution(
                result_table=result_table,
                supporting=supporting,
                final_viz=plan.final_viz,
                dashboard_requested=plan.dashboard_requested,
                tables=dict(tables),
                plan=plan,
                steps=executed,
                result_purpose=shown.purpose if shown else "",
                result_kind=tool_kind(shown) if shown else "transform",
            ),
            tables,
        )


def _input_for(
    step: ResearchStep,
    graph: PlanGraph,
    tables: dict[int, str],
    last_transform: str,
    source_table: str,
) -> str:
    if step.input == SOURCE:
        return source_table
    ref = _ref(step.input)
    if ref is not None:
        return tables.get(ref, last_transform)
    if step.input == PREVIOUS:
        transforms = [
            dependency
            for dependency in graph.nodes[step.step_id].depends_on
            if tables.get(dependency)
        ]
        if transforms:
            return tables[transforms[-1]]
    return last_transform


def _ref(value: str) -> int | None:
    if value.startswith("$step_") and value[6:].isdigit():
        return int(value[6:])
    if value.startswith("step_") and value[5:].isdigit():
        return int(value[5:])
    return None


def _progress(step: ResearchStep, status: str, **fields: object) -> dict:
    data: dict[str, object] = {
        "stage": "step",
        "status": status,
        "step_id": step.step_id,
        "tool": step.tool,
        "purpose": step.purpose,
        **fields,
    }
    return {
        "type": "progress",
        "data": {key: value for key, value in data.items() if value is not None},
    }
