"""Обработка ошибок шага: исправление по тексту ошибки, затем вопрос пользователю.

Повтор шага с теми же аргументами бесполезен: неверная колонка или тип
упадут так же. Поэтому упавший шаг вместе с текстом ошибки и колонками
входной таблицы отдаётся планировщику (StepPlanner.fix_step), и
исправленный шаг выполняется заново — до STEP_FIX_ATTEMPTS раз. Не
помогло — оркестратор просит пользователя уточнить (clarification_needed).

Сетевые ошибки LLM здесь не повторяются: их уже повторяет LLMClient.
"""

import duckdb

from app.models.research_step import ResearchStep

# Сколько раз исправить упавший шаг перед вопросом пользователю.
STEP_FIX_ATTEMPTS = 2

# Ошибки в аргументах шага: неверная колонка, тип, ссылка, выражение.
FIXABLE_ERRORS = (ValueError, duckdb.Error)


def fixable(step: ResearchStep, exc: Exception) -> bool:
    """Можно ли исправить шаг по этой ошибке.

    Шаг кодогенерации исправляет свой код сам (settings.codegen_attempts);
    NotImplementedError и прочие сбои (песочница, баг тула) исправлением
    аргументов не лечатся.
    """
    if step.needs_llm_codegen or not step.tool:
        return False
    return isinstance(exc, FIXABLE_ERRORS)
