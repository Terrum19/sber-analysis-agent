"""Главный цикл исследования (orchestrator).

Конвейер одного хода диалога:
  вопрос -> planner (LLM: план шагов) -> step_executor (DuckDB/тулы/песочница)
  -> insight (вывод) -> viz (график) / dashboards (панель) -> экспорт.

Каждый результат шага сохраняется в DuckDB как temp_result_vN и
передаётся на следующий шаг (см. core/memory/state_cache.py).
"""

from collections.abc import AsyncIterator

from app.api.schemas.chat import ChatRequest
from app.core.agent.insight import InsightGenerator
from app.core.agent.planner import StepPlanner
from app.core.agent.recovery import with_retry
from app.core.agent.step_executor import StepExecutor
from app.core.memory.session_memory import SessionMemory
from app.core.viz.chart_selector import select_chart
from app.core.viz.vega_spec_builder import build_vega_spec


class AgentOrchestrator:
    """Оркестратор одного хода диалога."""

    def __init__(self) -> None:
        self.planner = StepPlanner()
        self.executor = StepExecutor()
        self.insight_gen = InsightGenerator()

    async def run(self, request: ChatRequest) -> AsyncIterator[dict]:
        """Выполнить исследование и вернуть поток событий для фронта."""
        memory = SessionMemory(request.session_id)
        context = memory.build_context(request.question)

        plan = await self.planner.plan(request.question, context)
        result_table = "raw"
        for step in plan.steps:
            try:
                result_table = await with_retry(self.executor.execute, step, result_table)
            except Exception as exc:  # noqa: BLE001
                yield {"type": "error", "data": {"step": step.step_id, "message": str(exc)}}
                return
            memory.record_step(step, result_table)

        insight = await self.insight_gen.generate(request.question, result_table)
        yield {"type": "text", "data": {"headline": insight.headline, "details": insight.details}}

        chart_type = select_chart(request.question, result_table, plan.final_viz)
        spec = build_vega_spec(chart_type, result_table)
        yield {"type": "chart", "data": {"spec": spec, "caption": insight.headline}}

        if plan.dashboard_requested:
            from app.core.dashboards.generator import DashboardGenerator

            dashboard_id = await DashboardGenerator().generate(request.session_id, [spec])
            yield {"type": "dashboard", "data": {"dashboard_id": dashboard_id}}

        memory.record_answer(insight.headline)
        yield {"type": "done", "data": {}}
