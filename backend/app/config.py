"""Конфигурация приложения (pydantic-settings, читается из .env).

Все секреты/переключатели провайдеров LLM — только здесь.
Замкнутый контур: по умолчанию локальный LLM endpoint (Ollama/vLLM).
"""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_DIR.parent
# .env ищем не от текущей папки, а рядом с кодом: корень репозитория
# (docker-compose) и backend/ — значения из backend/.env важнее.
ENV_FILES = (_REPO_ROOT / ".env", _BACKEND_DIR / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore")

    # LLM (локально, без облака)
    llm_base_url: str = "http://localhost:11434/v1"  # Ollama / vLLM (OpenAI-совместимый)
    llm_model: str = "qwen2.5-coder:14b"
    # Ключ провайдера (OpenRouter и т.п.); локальным Ollama/vLLM не нужен.
    llm_api_key: SecretStr = SecretStr("not-needed")
    llm_timeout_s: int = 120
    llm_temperature: float = 0.0

    # Хранилище
    storage_dir: str = "backend/storage"
    duckdb_path: str = "backend/storage/duckdb/main.duckdb"
    sqlite_path: str = "backend/storage/sessions/sessions.sqlite"
    csv_max_upload_mb: int = 1024

    # Лимиты песочницы / агента
    wasm_timeout_s: int = 60
    wasm_mem_mb: int = 2048
    wasm_max_parallel: int = 1
    wasm_node_bin: str = "node"
    wasm_runtime_dir: str = ""
    codegen_enabled: bool = True
    codegen_attempts: int = 2
    codegen_max_rows: int = 200_000
    agent_max_steps: int = 12
    semantic_top_k: int = 5

    postgres_max_tables: int = 50

    profile_approx_quantile: bool = True
    profile_approx_min_rows: int = 100_000

    enrich_description_max_chars: int = 200
    enrich_batch_size: int = 25
    # Документ-описание таблицы длиннее порога обрезается перед отправкой в LLM.
    enrich_document_max_chars: int = 20_000

    @field_validator("storage_dir", "duckdb_path", "sqlite_path", "wasm_runtime_dir")
    @classmethod
    def _from_repo_root(cls, value: str) -> str:
        """Относительный путь считаем от корня репозитория, а не от текущей папки."""
        if not value or value == ":memory:" or Path(value).is_absolute():
            return value
        return str(_REPO_ROOT / value)


@lru_cache
def get_settings() -> Settings:
    """Кешированный доступ к настройкам."""
    return Settings()


def reload_settings() -> Settings:
    """Сбросить кэш настроек и перечитать .env (нужно после изменения переменных)."""
    get_settings.cache_clear()
    return get_settings()


settings = get_settings()
