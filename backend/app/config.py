"""Конфигурация приложения (pydantic-settings, читается из .env).

Все секреты/переключатели провайдеров LLM — только здесь.
Замкнутый контур: по умолчанию локальный LLM endpoint (Ollama/vLLM).
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
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
    # План на CPU строится 90-180 с (docs/reports/llm_perf); 120 с обрывали ход.
    llm_timeout_s: int = 600
    # Повторы клиента openai при 429, 5xx, обрыве и таймауте. Повтор таймаута
    # стоит ещё до llm_timeout_s, поэтому по умолчанию один: локальной модели
    # повторять нечего, облачному провайдеру с 429 можно поднять.
    llm_max_retries: int = 1
    llm_temperature: float = 0.0
    # Как просить structured output (docs/reports/llm_perf, П2-П4):
    # json_schema — response_format=json_schema (грамматика на стороне
    # Ollama/vLLM) + толерантный разбор + один переспрос с ошибкой;
    # tools — instructor TOOLS с fallback-цепочкой. Локальный Qwen
    # tool_call не возвращает, и каждый ответ стоил три вызова.
    llm_structured_mode: Literal["json_schema", "tools"] = "json_schema"
    # Предел токенов ответа (None — без предела; Ollama тогда может
    # генерировать до конца контекста).
    llm_max_tokens: int | None = None
    # Исправления качества плана и инсайта (app/core/agent/quality_fixes.py).
    agent_quality_fixes: bool = True
    # H-R1: в промпт планировщика — только релевантные вопросу тулы.
    # Быстрее вдвое, но сломал вопрос про тренд (П8) — выключено.
    agent_tool_topk: bool = False
    # Контекст модели в токенах. Задаётся на сервере (Ollama — OLLAMA_CONTEXT_LENGTH,
    # vLLM — --max-model-len), здесь — сколько ждём; от него считается бюджет промпта.
    llm_num_ctx: int = 16384
    # Ollama: скачать LLM_MODEL, если её нет, и работать с её вариантом с
    # контекстом llm_num_ctx (core/llm/ollama_setup.py). Другим серверам не мешает.
    llm_prepare_model: bool = True
    # Символов на токен для оценки длины промпта: с запасом под кириллицу (~2,6).
    llm_chars_per_token: float = 2.5
    # Эмбеддинги (OpenAI-совместимый /embeddings): по смыслу вопроса агент
    # переходит к другим данным посреди сессии. Пусто — только по словам.
    # Ollama в compose скачивает ту же EMBED_MODEL (infra/ollama/entrypoint.sh).
    embed_model: str = ""
    # Сервер и ключ эмбеддингов; пусто — те же, что у LLM.
    embed_base_url: str = ""
    embed_api_key: SecretStr | None = None
    embed_timeout_s: int = 30

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Хранилище
    storage_dir: str = "backend/storage"
    duckdb_path: str = "backend/storage/duckdb/main.duckdb"
    sqlite_path: str = "backend/storage/sessions/sessions.sqlite"
    csv_max_upload_mb: int = 1024
    result_ttl_s: int = 6 * 3600

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
    # Сколько последних ходов сессии (любого статуса) видит планировщик (#197).
    context_turns: int = 5
    # Сколько последних включённых воспоминаний сессии идёт в промпты (#197).
    context_memories: int = 20
    semantic_top_k: int = 5
    # Сколько колонок вне топ-K перечислить планировщику по именам (0 — не показывать).
    semantic_other_columns: int = 80

    # POSTGRES_MAX_TABLES — прежнее имя; DB_MAX_TABLES важнее, если заданы оба.
    db_max_tables: int = Field(
        default=50, validation_alias=AliasChoices("DB_MAX_TABLES", "POSTGRES_MAX_TABLES")
    )
    postgres_restore_timeout_s: int = 60

    profile_approx_quantile: bool = True
    profile_approx_min_rows: int = 100_000
    # Литералы пропусков в тексте: профайлер их считает, загрузчик CSV (D-08) чистит.
    csv_null_tokens: list[str] = ["NULL", "null", "None", "NaN", "N/A", "n/a", "#N/A", "-"]
    # Минимальная доля значений для приведения VARCHAR к типу (D-08).
    csv_retype_min_share: float = 1.0

    enrich_description_max_chars: int = 200
    enrich_batch_size: int = 25
    # Документ-описание таблицы длиннее порога обрезается перед отправкой в LLM.
    enrich_document_max_chars: int = 20_000

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

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
