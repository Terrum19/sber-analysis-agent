"""Главный цикл исследования (orchestrator).

Конвейер одного хода диалога:
  вопрос -> planner (LLM: план шагов) -> step_executor (DuckDB/тулы/песочница)
  -> insight (вывод) -> viz (график) / dashboards (панель) -> экспорт.

Каждый результат шага сохраняется в DuckDB как temp_result_vN и
передаётся на следующий шаг (см. core/memory/state_cache.py).

При сбое шага после 2-х попыток orquestратор выдаёт
SSE-событие ``clarification_needed`` и сохраняет в SessionMemory
состояние ожидания уточнения (``pending_recovery``). При следующем
запросе от того же ``session_id`` с полем ``recovery_response``
оркестратор загружает сохранённый контекст, применяет ответ
пользователя и повторяет только неудачный шаг (или перепланирует).
"""

import json
import logging
from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

from app.api.schemas.chat import ChatRequest
from app.core.agent.insight import InsightGenerator
from app.core.agent.planner import StepPlanner
from app.core.agent.recovery import with_retry
from app.core.agent.step_executor import StepExecutor
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory.session_memory import SessionMemory
from app.core.viz.chart_selector import select_chart
from app.core.viz.vega_spec_builder import build_vega_spec

# Количество попыток вызова инструмента перед уточнением у пользователя.
STEP_RETRY_ATTEMPTS = 2

# Готовые варианты ответа, которые фронт отображает кнопками.
CLARIFICATION_SUGGESTIONS = [
    "Переформулировать вопрос",
    "Уточнить колонки",
    "Пропустить этот шаг и продолжить",
]


class ClarificationNeeded(Exception):
    """Исключение: шаг не прошёл после N попыток, нужен уточняющий ответ пользователя."""

    def __init__(self, step_id: int, error: str, suggestions: list[str]) -> None:
        self.step_id = step_id
        self.error = error
        self.suggestions = suggestions
        super().__init__(error)


class AgentOrchestrator:
    """Оркестратор одного хода диалога."""

    def __init__(
        self,
        planner: StepPlanner | None = None,
        executor: StepExecutor | None = None,
        insight_gen: InsightGenerator | None = None,
        db: DuckDBManager | None = None,
    ) -> None:
        import app.core.data.duckdb_manager as _duckdb_module
        DuckDBManager = _duckdb_module.DuckDBManager
        self.db = db or DuckDBManager()
        self.planner = planner or StepPlanner()
        self.executor = executor or StepExecutor()
        self.insight_gen = insight_gen or InsightGenerator(db=self.db)

    async def run(self, request: ChatRequest) -> AsyncIterator[dict]:
        """Выполнить исследование и вернуть поток событий для фронта."""
        logger.info(">>> run() session_id=%s question=%r", request.session_id, request.question)
        memory = SessionMemory(request.session_id)

        # --- Возобновление после уточнения (pending_recovery) ---
        pending = memory.get_pending_recovery()
        try:
            if pending is not None:
                if not request.recovery_response:
                    yield {
                        "type": "error",
                        "data": {
                            "message": "Требуется уточнение. Отправьте ответ на предыдущий вопрос агента.",
                        },
                    }
                    return
                result_table = await self._apply_recovery(memory, pending, request)
                memory.clear_pending_recovery()
            else:
                # Обычный путь: новый вопрос -> план -> выполнение шагов.
                context = memory.build_context(request.question)
                logger.info(">>> run() планируем шаги (planner.plan)")
                plan = await self.planner.plan(request.question, context)
                logger.info("<<< run() план получен: шагов=%d final_viz=%s dashboard=%s",
                            len(plan.steps), plan.final_viz, plan.dashboard_requested)
                for i, s in enumerate(plan.steps):
                    logger.info("    шаг %d: tool=%s columns=%s needs_codegen=%s purpose=%r",
                                s.step_id, s.tool, s.columns, s.needs_llm_codegen, s.purpose)
                datasource = memory._state.get("datasource", "raw")
                result_table = await self._execute_plan(memory, plan, datasource, request.question)
        except ClarificationNeeded as exc:
            yield {
                "type": "clarification_needed",
                "data": {
                    "step_id": exc.step_id,
                    "error": exc.error,
                    "suggestions": exc.suggestions,
                },
            }
            return

        # --- Финальный блок: инсайт -> чарт -> дашборд -> done ---
        logger.info(">>> run() генерация инсайта")
        insight = await self.insight_gen.generate(request.question, result_table)
        logger.info("<<< run() инсайт: headline=%r details=%r", insight.headline, insight.details)
        yield {"type": "text", "data": {"headline": insight.headline, "details": insight.details}}

        final_viz = pending.get("final_viz", "auto") if pending else plan.final_viz  # type: ignore[possibly-undefined]
        chart_type = select_chart(request.question, result_table, final_viz)
        logger.info(">>> run() построение графика chart_type=%s", chart_type)
        data = self.db.to_records(result_table)
        spec = build_vega_spec(chart_type, result_table=result_table, data=data)
        logger.info("<<< run() график построен: mark=%s строк=%d",
                    spec.get("mark", "?"), len(spec.get("data", {}).get("values", [])))
        yield {"type": "chart", "data": {"spec": spec, "caption": insight.headline}}

        dashboard_requested = pending.get("dashboard_requested", False) if pending else plan.dashboard_requested  # type: ignore[possibly-undefined]
        if dashboard_requested:
            from app.core.dashboards.generator import DashboardGenerator

            dashboard_id = await DashboardGenerator().generate(request.session_id, [spec])
            yield {"type": "dashboard", "data": {"dashboard_id": dashboard_id}}

        memory.record_answer(insight.headline)
        yield {"type": "done", "data": {}}

    # --- Вспомогательные методы ---

    async def _execute_plan(
        self,
        memory: SessionMemory,
        plan,
        input_table: str,
        question: str,
    ) -> str:
        """Последовательно выполнить все шаги плана.

        При сбое шага после STEP_RETRY_ATTEMPTS попыток сохраняет
        pending_recovery в SessionMemory и выбрасывает ClarificationNeeded,
        чтобы вызывающий код мог выдать clarification_needed и прервать генератор.
        """
        result_table = input_table
        for step in plan.steps:
            logger.info(">>> _execute_plan шаг %d: tool=%s columns=%s needs_codegen=%s purpose=%r input_table=%s",
                        step.step_id, step.tool, step.columns, step.needs_llm_codegen, step.purpose, result_table)
            try:
                result_table = await with_retry(
                    self.executor.execute,
                    step,
                    result_table,
                    attempts=STEP_RETRY_ATTEMPTS,
                )
            except Exception as exc:  # noqa: BLE001
                # Сохраняем контекст неудачи для последующего уточнения.
                memory.set_pending_recovery(
                    {
                        "question": question,
                        "failed_step_id": step.step_id,
                        "plan_steps": [s.model_dump() for s in plan.steps],
                        "input_table": result_table,
                        "error": str(exc),
                        "attempts_made": STEP_RETRY_ATTEMPTS,
                        "suggestions": CLARIFICATION_SUGGESTIONS,
                        "final_viz": plan.final_viz,
                        "dashboard_requested": plan.dashboard_requested,
                    }
                )
                logger.error("<<< _execute_plan шаг %d ПАЛ: %r", step.step_id, exc)
                raise ClarificationNeeded(
                    step_id=step.step_id,
                    error=str(exc),
                    suggestions=CLARIFICATION_SUGGESTIONS,
                )
            logger.info("<<< _execute_plan шаг %d УСПЕШЕН result_table=%s",
                        step.step_id, result_table)
            memory.record_step(step, result_table)
        logger.info("<<< _execute_plan все шаги завершены final_table=%s", result_table)
        return result_table

    async def _apply_recovery(
        self,
        memory: SessionMemory,
        pending: dict,
        request: ChatRequest,
    ) -> str:
        """Применить ответ пользователя на уточнение и продолжить выполнение.

        Поддерживаемые варианты recovery_response:
          - JSON с полем ``action``: ``"skip_step"`` — пропустить неудачный шаг.
          - Свободный текст (любая другая строка) — перепланирование с учётом
            текста уточнения и контекста предыдущих шагов.
        """
        recovery_text = request.recovery_response or ""
        action = None
        free_text = recovery_text

        # Пытаемся распарсить JSON с действием (кнопки фронта).
        try:
            parsed = json.loads(recovery_text)
            if isinstance(parsed, dict) and "action" in parsed:
                action = parsed["action"]
                free_text = parsed.get("text", "")
        except (json.JSONDecodeError, TypeError):
            action = None
            free_text = recovery_text

        failed_step_id = pending["failed_step_id"]
        plan_steps = pending["plan_steps"]
        input_table = pending["input_table"]

        if action == "skip_step":
            # Пропускаем неудачный шаг, выполняем остальные из сохранённого плана.
            remaining_steps = [
                s for s in plan_steps if s.get("step_id", -1) > failed_step_id
            ]
            # Превращаем словари обратно в ResearchStep.
            from app.models.research_step import ResearchPlan, ResearchStep

            steps = [ResearchStep.model_validate(s) for s in remaining_steps]
            plan = ResearchPlan(
                steps=steps,
                final_viz=pending.get("final_viz", "auto"),
                dashboard_requested=pending.get("dashboard_requested", False),
            )
            return await self._execute_plan(memory, plan, input_table, request.question)

        # Перепланирование: формируем уточнённый вопрос с учётом ответа
        # пользователя и контекста предыдущих шагов.
        from app.models.research_step import ResearchPlan, ResearchStep

        clarification_note = ""
        if free_text:
            clarification_note = f"\nУточнение пользователя: {free_text}"

        modified_question = (
            f"{request.question}{clarification_note}"
            if request.question
            else free_text
        )

        # Контекст включает историю шагов, выполненных до сбоя, чтобы LLM могла
        # спланировать обход или исправление неудачного шага.
        prior_steps = [s for s in plan_steps if s.get("step_id", -1) < failed_step_id]
        steps_summary = "; ".join(
            f"шаг {s.get('step_id')}: {s.get('purpose', '')}" for s in prior_steps
        )
        error_note = f"\nОшибка на шаге {failed_step_id}: {pending['error']}"

        context = memory.build_context(modified_question)
        context["summary"] = (
            f"{context.get('summary', '')} | {steps_summary}{error_note}".strip(" |")
        )

        plan = await self.planner.plan(modified_question, context)
        return await self._execute_plan(memory, plan, input_table, modified_question)
