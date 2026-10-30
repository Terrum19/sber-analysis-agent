"""Схема состояния LLM для фронта (контракт фронтенд <-> бэкенд)."""

from typing import Literal

from pydantic import BaseModel


class LLMStatusOut(BaseModel):
    """Готова ли модель: connecting — Ollama не отвечает, pulling — модель скачивается
    (progress — доля 0..1), preparing — создаётся вариант с контекстом, missing — модели
    LLM_MODEL нет в реестре Ollama (опечатка), ok — можно спрашивать.
    detail — причина, если шаг не удался (например, нет интернета для скачивания)."""

    state: Literal["connecting", "pulling", "preparing", "missing", "ok"]
    model: str
    progress: float | None = None
    detail: str | None = None
    # P0: наблюдаемость конфигурации без секретов.
    context: int | None = None
    structured_mode: str | None = None
    max_tokens: int | None = None
    last_latency_ms: int | None = None
