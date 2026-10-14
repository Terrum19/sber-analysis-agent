"""Конфигурация приложения (pydantic-settings, читается из .env).

Все секреты/переключатели провайдеров LLM — только здесь.
Замкнутый контур: по умолчанию локальный LLM endpoint (Ollama/vLLM).
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM (локально, без облака)
    llm_base_url: str = "http://localhost:11434/v1"  # Ollama / vLLM (OpenAI-совместимый)
    llm_model: str = "qwen2.5-coder:14b"
    llm_timeout_s: int = 120
    llm_temperature: float = 0.0

    # Хранилище
    storage_dir: str = "backend/storage"
    duckdb_path: str = "backend/storage/duckdb/main.duckdb"
    sqlite_path: str = "backend/storage/sessions/sessions.sqlite"

    # Лимиты песочницы / агента
    wasm_timeout_s: int = 30
    wasm_mem_mb: int = 512
    agent_max_steps: int = 12
    semantic_top_k: int = 5


@lru_cache
def get_settings() -> Settings:
    """Кешированный доступ к настройкам."""
    return Settings()


settings = get_settings()
