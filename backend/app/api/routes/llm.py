"""Состояние LLM: фронт показывает его индикатором над чатом (#167).

Модель готовит бэкенд сам (core/llm/ollama_setup.py): пока она скачивается
или Ollama не отвечает, пользователь видит, что происходит, а не ошибку хода.
"""

from fastapi import APIRouter

from app.api.schemas.llm import LLMStatusOut
from app.config import settings
from app.core.agent.metrics import last_latency_ms
from app.core.llm import budget, ollama_setup

router = APIRouter()


@router.get("/status", response_model=LLMStatusOut)
def llm_status() -> LLMStatusOut:
    """Готова ли LLM отвечать на вопросы и в каком режиме работает бэкенд."""
    return LLMStatusOut(
        **ollama_setup.status(),
        context=budget.server_context() or settings.llm_num_ctx,
        structured_mode=settings.llm_structured_mode,
        max_tokens=settings.llm_max_tokens,
        last_latency_ms=last_latency_ms(),
    )
