"""Планировщик: вопрос пользователя -> ResearchPlan (список шагов).

Отдаёт LLM вопрос, топ-K релевантных колонок и список тулов.
Результат валидируется по ResearchPlan (см. models/research_step.py).
"""

import json
import logging

from app.core.llm.client import LLMClient

logger = logging.getLogger(__name__)
from app.core.agent.step_executor import tool_kind
from app.core.data.semantic_select import describe_columns, select_columns
from app.core.llm.prompts.step_planner import PLAN_ERRORS, STEP_FIX, STEP_PLANNER
from app.core.tools.registry import tool_registry
from app.models.research_step import PREVIOUS, SOURCE, ResearchPlan, ResearchStep

# Сколько раз переспросить LLM, если план не прошёл валидацию.
PLAN_FIX_ATTEMPTS = 1


class StepPlanner:
    """Планировщик исследования на базе LLM."""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or LLMClient()

    async def plan(self, question: str, context: dict) -> ResearchPlan:
        """Сгенерировать план шагов под вопрос."""
        tools = tool_registry.describe()
        columns = context.get("columns", [])
        profile = context.get("profile")
        if profile is not None:
            # Отбираем топ-5 релевантных колонок (семантический отбор, docs/architecture.md)
            columns = select_columns(
                question, columns, profile=profile, aliases=context.get("aliases")
            )
        start = int(context.get("next_step_id", 0))
        prompt = STEP_PLANNER.format(
            tools=tools,
            columns=describe_columns(columns, profile, context.get("descriptions")),
            context=context.get("summary", ""),
            executed=context.get("executed", "нет"),
            next_step_id=start,
            question=question,
        )
        logger.info(">>> StepPlanner.plan() вопрос=%r", question)
        logger.debug("    промпт=%r", prompt)
        plan = await self._ask(prompt, start)
        for _ in range(PLAN_FIX_ATTEMPTS):
            errors = validate_plan(plan, known_steps=start)
            if not errors:
                break
            logger.warning("    план не прошёл валидацию: %s", errors)
            fix = PLAN_ERRORS.format(plan=_plan_json(plan), errors="\n".join(errors))
            plan = await self._ask(prompt + fix, start)
        logger.info("<<< StepPlanner.plan() шагов=%d", len(plan.steps))
        return plan

    async def fix_step(
        self, step: ResearchStep, error: str, columns: dict[str, str], seen: set[int]
    ) -> ResearchStep:
        """Исправить упавший шаг по тексту ошибки и колонкам его входной таблицы.

        step_id и purpose сохраняются, инструмент может смениться. Шаг,
        не прошедший валидацию, — ValueError с её ошибками (их получит
        следующая попытка исправления).
        """
        from app.core.llm.prompts.system_analyst import SYSTEM_ANALYST

        prompt = STEP_FIX.format(
            tools=tool_registry.describe(),
            step=_step_json(step),
            error=error,
            columns="\n".join(f"{name}: {kind}" for name, kind in columns.items()) or "нет",
            executed=", ".join(f"$step_{ref}" for ref in sorted(seen)) or "нет",
        )
        logger.info(">>> StepPlanner.fix_step() шаг %d ошибка=%r", step.step_id, error)
        fixed = await self.llm.complete_structured(SYSTEM_ANALYST, prompt, ResearchStep)
        fixed = fixed.model_copy(
            update={"step_id": step.step_id, "purpose": fixed.purpose or step.purpose}
        )
        errors = _validate_step(fixed, seen)
        if errors:
            raise ValueError("; ".join(errors))
        logger.info("<<< StepPlanner.fix_step() tool=%s args=%s", fixed.tool, fixed.arguments)
        return fixed

    async def _ask(self, prompt: str, start: int) -> ResearchPlan:
        from app.core.llm.prompts.system_analyst import SYSTEM_ANALYST

        plan = await self.llm.complete_structured(SYSTEM_ANALYST, prompt, ResearchPlan)
        # LLM может не включать step_id в шаги — присваиваем последовательные ID.
        for i, step in enumerate(plan.steps):
            step.step_id = start + i
        return plan


def _step_dict(step: ResearchStep) -> dict:
    return {
        "tool": step.tool,
        "input": step.input,
        "arguments": step.arguments,
        "purpose": step.purpose,
    }


def _step_json(step: ResearchStep) -> str:
    return json.dumps(_step_dict(step), ensure_ascii=False, default=str)


def _plan_json(plan: ResearchPlan) -> str:
    """План в том виде, в каком его просили у модели (для переспроса)."""
    steps = [_step_dict(s) for s in plan.steps]
    payload = {"steps": steps, "final_viz": plan.final_viz, "result_step": plan.result_step}
    return json.dumps(payload, ensure_ascii=False, default=str)


_PLACEHOLDER = "ref_table"


def _without_refs(value: object) -> object:
    """Заменить ссылки $step_N/$prev/$source на валидное имя (для проверки)."""
    if isinstance(value, list):
        return [_without_refs(item) for item in value]
    if isinstance(value, dict):
        return {key: _without_refs(item) for key, item in value.items()}
    if isinstance(value, str) and (value in (PREVIOUS, SOURCE) or value.startswith("$step_")):
        return _PLACEHOLDER
    return value


def validate_plan(plan: ResearchPlan, known_steps: int = 0) -> list[str]:
    """Проверить шаги до выполнения: инструмент, аргументы, ссылки на шаги."""
    errors: list[str] = []
    seen = set(range(known_steps))
    for step in plan.steps:
        errors.extend(_validate_step(step, seen))
        seen.add(step.step_id)
    # План, который кончается разведкой (lookup), всё равно будет достроен —
    # result_step может указывать на будущие шаги, сбрасываем его без переспроса.
    if plan.steps and tool_kind(plan.steps[-1]) == "lookup" and plan.result_step not in seen:
        plan.result_step = None
    if plan.result_step is not None and plan.result_step not in seen:
        errors.append(f"result_step={plan.result_step}: такого шага нет")
    return errors


def _validate_step(step: ResearchStep, seen: set[int]) -> list[str]:
    prefix = f"шаг {step.step_id}"
    if step.needs_llm_codegen or not step.tool:
        return [f"{prefix}: не указан инструмент (кодогенерация не поддерживается)"]
    try:
        tool = tool_registry.get(step.tool)
    except KeyError:
        return [f"{prefix}: неизвестный инструмент {step.tool!r}"]
    errors = []
    step_input = f"${step.input}" if step.input.startswith("step_") else step.input
    for ref in _step_refs([step_input, step.arguments, step.columns]):
        if ref not in seen:
            errors.append(f"{prefix}: ссылка $step_{ref} на ещё не выполненный шаг")
    try:
        tool.parse_args(_without_refs(step.columns), _without_refs(step.arguments))
    except ValueError as exc:
        errors.append(f"{prefix}: {exc}")
    return errors


def _step_refs(value: object) -> list[int]:
    if isinstance(value, list):
        return [ref for item in value for ref in _step_refs(item)]
    if isinstance(value, dict):
        return [ref for item in value.values() for ref in _step_refs(item)]
    if isinstance(value, str) and value.startswith("$step_") and value[6:].isdigit():
        return [int(value[6:])]
    return []
