"""Главный цикл исследования (orchestrator).

Конвейер одного хода диалога:
  вопрос -> planner (LLM: план шагов) -> step_executor (DuckDB/тулы/песочница)
  -> insight (вывод) -> viz (график) / dashboards (панель) -> экспорт.

Каждый результат шага сохраняется в DuckDB как temp_result_vN и
передаётся на следующий шаг (см. core/memory/state_cache.py). Результат
тула-отчёта (стат-тест, поиск значений, паспорт таблицы) на следующий шаг
не передаётся: тот получает таблицу, которую отчёт анализировал.

Просьба сменить вид прошлого графика («покажи в другом виде», «сделай
таблицей») обслуживается без планировщика и без повторного SQL: спека
перестраивается chart_morph из кешированной temp_result_vN.
"""

from collections.abc import AsyncIterator

import duckdb

from app.api.schemas.chat import ChatRequest
from app.core.agent.insight import InsightGenerator
from app.core.agent.planner import StepPlanner
from app.core.agent.recovery import with_retry
from app.core.agent.step_executor import StepExecutor, transforms_data
from app.core.memory.profile_memory import ProfileMemory
from app.core.memory.session_memory import SessionMemory
from app.core.memory.state_cache import StateCache
from app.core.viz.chart_morph import detect_morph, morph
from app.core.viz.chart_selector import select_chart
from app.core.viz.vega_spec_builder import SPEC_ROW_LIMIT, build_vega_spec, column_aliases

SOURCE_TABLE = "raw"


class AgentOrchestrator:
    """Оркестратор одного хода диалога."""

    def __init__(
        self,
        planner: StepPlanner | None = None,
        executor: StepExecutor | None = None,
        insight_gen: InsightGenerator | None = None,
        cache: StateCache | None = None,
    ) -> None:
        self.planner = planner or StepPlanner()
        self.executor = executor or StepExecutor()
        self.insight_gen = insight_gen or InsightGenerator()
        self.cache = cache or StateCache(self.executor.db)
        self.profiles = ProfileMemory(self.executor.db)

    async def run(self, request: ChatRequest) -> AsyncIterator[dict]:
        """Выполнить исследование и вернуть поток событий для фронта."""
        memory = SessionMemory(request.session_id)
        titles = column_aliases(
            {
                "glossary": self.profiles.load_glossary(SOURCE_TABLE),
                "aliases": {
                    row.column: row.aliases for row in self.profiles.load_enrichment(SOURCE_TABLE)
                },
            }
        )

        morphed = self._morph(memory, request.question, titles)
        if morphed is not None:
            yield {"type": "chart", "data": morphed}
            yield {"type": "done", "data": {}}
            return

        context = memory.build_context(request.question)
        plan = await self.planner.plan(request.question, context)
        data_table = result_table = SOURCE_TABLE
        for step in plan.steps:
            try:
                result_table = await with_retry(self.executor.execute, step, data_table)
            except Exception as exc:  # noqa: BLE001
                yield {"type": "error", "data": {"step": step.step_id, "message": str(exc)}}
                return
            if transforms_data(step):
                data_table = result_table
            memory.record_step(step, result_table)

        insight = await self.insight_gen.generate(request.question, result_table)
        yield {"type": "text", "data": {"headline": insight.headline, "details": insight.details}}

        chart_type = select_chart(request.question, result_table, plan.final_viz)
        spec = build_vega_spec(chart_type, result_table, db=self.cache.db, titles=titles)
        memory.record_chart(spec["usermeta"]["chart_type"], result_table, insight.headline)
        yield {"type": "chart", "data": {"spec": spec, "caption": insight.headline}}

        if plan.dashboard_requested:
            from app.core.dashboards.generator import DashboardGenerator

            dashboard_id = await DashboardGenerator().generate(request.session_id, [spec])
            yield {"type": "dashboard", "data": {"dashboard_id": dashboard_id}}

        memory.record_answer(insight.headline)
        yield {"type": "done", "data": {}}

    def _morph(self, memory: SessionMemory, question: str, titles: dict) -> dict | None:
        """Сменить вид прошлого графика из кеша (None — нужен новый план)."""
        target = detect_morph(question)
        last = memory.last_chart()
        if target is None or last is None:
            return None
        try:
            records = self.cache.get(last["result_table"], limit=SPEC_ROW_LIMIT)
        except duckdb.Error:
            return None
        spec = morph(last["chart_type"], target, records, titles, last["result_table"])
        chart_type = spec["usermeta"]["chart_type"]
        memory.record_chart(chart_type, last["result_table"], last["caption"])
        memory.record_answer(f"График перестроен: {chart_type}")
        return {"spec": spec, "caption": last["caption"]}
