"""Схема состояния LLM для фронта (контракт фронтенд <-> бэкенд)."""

from typing import Literal

from pydantic import BaseModel


class LLMStatusOut(BaseModel):
    """Готова ли модель: connecting — Ollama не отвечает, pulling — модель скачивается
    (progress — доля 0..1), preparing — создаётся вариант с контекстом, ok — можно спрашивать.
    detail — причина, если шаг не удался (например, нет интернета для скачивания)."""

    state: Literal["connecting", "pulling", "preparing", "ok"]
    model: str
    progress: float | None = None
    detail: str | None = None
