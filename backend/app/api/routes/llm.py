"""Состояние LLM: фронт показывает его индикатором над чатом (#167).

Модель готовит бэкенд сам (core/llm/ollama_setup.py): пока она скачивается
или Ollama не отвечает, пользователь видит, что происходит, а не ошибку хода.
"""

from fastapi import APIRouter

from app.api.schemas.llm import LLMStatusOut
from app.core.llm import ollama_setup

router = APIRouter()


@router.get("/status", response_model=LLMStatusOut)
def llm_status() -> LLMStatusOut:
    """Готова ли LLM отвечать на вопросы."""
    return LLMStatusOut(**ollama_setup.status())
