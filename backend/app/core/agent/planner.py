"""Планировщик: вопрос пользователя -> ResearchPlan (список шагов).

Отдаёт LLM вопрос, топ-K релевантных колонок и список тулов.
Результат валидируется по ResearchPlan (см. models/research_step.py).
"""
from app.core.llm.prompts.step_planner import STEP_PLANNER
from app.core.llm.client import LLMClient
from app.core.tools.registry import tool_registry
from app.models.research_step import ResearchPlan


class StepPlanner:
    """Планировщик исследования на базе LLM."""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or LLMClient()

    async def plan(self, question: str, context: dict) -> ResearchPlan:
        """Сгенерировать план шагов под вопрос."""
        from app.core.llm.prompts.system_analyst import SYSTEM_ANALYST

        tools = tool_registry.describe()
        prompt = STEP_PLANNER.format(
            tools=tools,
            columns=context.get("columns", []),
            context=context.get("summary", ""),
            question=question,
        )
        return await self.llm.complete_structured(SYSTEM_ANALYST, prompt, ResearchPlan)
