"""Конфигурация приложения (pydantic-settings, читается из .env).

Все секреты/переключатели провайдеров LLM — только здесь.
Замкнутый контур: по умолчанию локальный LLM endpoint (Ollama/vLLM).
"""

import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_DIR.parent
# .env ищем не от текущей папки, а рядом с кодом: корень репозитория
# (docker-compose) и backend/ — значения из backend/.env важнее.
ENV_FILES = (_REPO_ROOT / ".env", _BACKEND_DIR / ".env")


class Settings(BaseSettings):
    # populate_by_name — чтобы настройки с validation_alias (llm_reasoning_effort)
    # по-прежнему задавались и по имени поля, как в остальных тестах.
    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore", populate_by_name=True)

    # LLM (локально, без облака)
    llm_base_url: str = "http://localhost:11434/v1"  # Ollama / vLLM (OpenAI-совместимый)
    llm_model: str = "qwen2.5-coder:14b"
    # P1: пул моделей по ролям. Пустое значение — использовать llm_model/llm_base_url.
    llm_model_planner: str = ""
    llm_model_fast: str = ""
    llm_model_sql: str = ""
    llm_base_url_fast: str = ""
    llm_base_url_sql: str = ""
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
    # json_object — только JSON-режим без грамматики (провайдеры, которые
    # не поддерживают json_schema); tools — instructor TOOLS с fallback-цепочкой.
    # Локальный Qwen tool_call не возвращает, и каждый ответ стоил три вызова.
    llm_structured_mode: Literal["json_schema", "tools", "json_object"] = "json_schema"
    # Предел токенов ответа. Без предела Ollama генерирует до конца контекста,
    # и один ход может стоить минуты только на генерации.
    llm_max_tokens: int = 1536
    # Reasoning-модели (Qwen3 и т.п. через OpenRouter) тратят max_tokens на
    # скрытые рассуждения и возвращают пустой content с finish_reason=length.
    # В этом случае — один повтор с увеличенным лимитом; 0 отключает повтор.
    llm_length_retry_tokens: int = 4096
    # Сила размышлений думающей модели (OpenRouter, reasoning.effort): minimal,
    # low, medium, high; none (или off) — размышления выключены. None — параметр
    # не передаётся, решает провайдер. Ollama/vLLM его не понимают.
    # LLM_REASONING — прежнее имя переменной: старые .env писали его без
    # _EFFORT, и настройка молча не применялась — рассуждения съедали весь
    # max_tokens, а structured-ответ приходил пустым.
    llm_reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] | None = Field(
        default=None,
        validation_alias=AliasChoices("LLM_REASONING_EFFORT", "LLM_REASONING"),
    )
    # Провайдеры OpenRouter через запятую (provider.only), например deepinfra.
    # С размышлениями часть провайдеров ломает json_schema-план; None — выбирает
    # OpenRouter. Ollama/vLLM параметр не понимают.
    llm_provider: str | None = None
    # Срок одного вызова модели на выбор графика (ответ в чате, плитки
    # дашборда), с. Не уложилась — повторный вызов, и снова нет — вид без LLM,
    # а не ход на двадцать минут (OpenRouter держит запрос пробелами).
    chart_choice_timeout_s: float = 90
    # Исправления качества плана и инсайта (app/core/agent/quality_fixes.py).
    agent_quality_fixes: bool = True
    # Стриминг финального вывода: text {chunk} в SSE вместо одного text {headline}.
    # Выключить, если выбранная модель плохо стримит через Ollama.
    insight_stream: bool = True
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
    # --- Бюджет хода (P0) ---
    # Общий дедлайн хода, секунды. 0 — без общего дедлайна: упор на качество,
    # ход идёт столько, сколько нужно. От зависаний защищают лимиты этапов,
    # таймауты LLM/шага/инсайта/дашборда и счётчики циклов (шаги, replan,
    # исправления, toolgen, вызовы LLM).
    agent_turn_timeout_s: int = 0
    agent_plan_timeout_s: int = 240
    agent_step_timeout_s: int = 60
    # Один шаг может исправляться планировщиком; общий дедлайн не главный.
    agent_step_fix_attempts: int = 1
    agent_insight_timeout_s: int = 90
    agent_dashboard_timeout_s: int = 120
    # Страховка от зацикливания: максимум вызовов LLM за ход. Не ограничивает
    # качество на коротких вопросах, но не даёт бесконечно ходить по кругу.
    agent_max_llm_calls: int = 24
    agent_max_steps: int = 8
    # Самосоздание инструментов (toolgen): спека + SQL или Python в WASM.
    toolgen_enabled: bool = True
    toolgen_attempts: int = 2
    toolgen_sample_rows: int = 500
    toolgen_max_tools: int = 25
    toolgen_max_per_turn: int = 2
    toolgen_store: bool = True
    toolgen_max_sql_chars: int = 4000
    toolgen_max_code_chars: int = 6000
    # Сколько последних ходов сессии (любого статуса) видит планировщик (#197).
    context_turns: int = 5
    # Сколько последних включённых воспоминаний сессии идёт в промпты (#197).
    context_memories: int = 20
    agent_max_parallel_llm: int = 2
    agent_max_parallel_steps: int = 2
    agent_fast_insight: bool = True
    agent_simple_router: bool = True
    # P1-флаги: по умолчанию выключены, чтобы не менять поведение P0.
    agent_semantic_layer: bool = False
    agent_dag_executor: bool = False
    agent_typed_schema: bool = False
    agent_plan_cache: bool = False
    agent_result_cache: bool = False
    agent_cache_ttl_s: int = 3600
    agent_metrics_store: bool = True
    sql_timeout_s: int = 60
    # Лимит на достройку плана после lookup-шага: каждый replan — ещё LLM-вызов.
    agent_max_replans: int = 1
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

    @field_validator("llm_reasoning_effort", mode="before")
    @classmethod
    def _reasoning_effort(cls, value: object) -> object:
        """off — прежнее имя выключенных размышлений; регистр и пробелы не важны."""
        if isinstance(value, str):
            text = value.strip().lower()
            return "none" if text == "off" else text or None
        return value

    @field_validator("storage_dir", "duckdb_path", "sqlite_path", "wasm_runtime_dir")
    @classmethod
    def _from_repo_root(cls, value: str) -> str:
        """Относительный путь считаем от корня репозитория, а не от текущей папки."""
        if not value or value == ":memory:" or Path(value).is_absolute():
            return value
        return str(_REPO_ROOT / value)

    @field_validator("llm_base_url", "llm_base_url_fast", "llm_base_url_sql")
    @classmethod
    def _normalize_base_url(cls, value: str) -> str:
        """Привести OpenAI-совместимый URL к виду ``.../v1``.

        Пользователи копируют полный адрес ``/v1/chat/completions`` из
        документации или OpenRouter — клиент openai добавляет путь сам, и
        запрос уходил бы на ``/chat/completions/chat/completions``.
        """
        text = (value or "").strip().rstrip("/")
        if not text:
            return text
        text = text.removesuffix("/chat/completions")
        if not text.endswith("/v1"):
            logger.warning("LLM base URL не заканчивается на /v1: %s", text)
        return text

    @field_validator("agent_turn_timeout_s")
    @classmethod
    def _turn_timeout_can_be_disabled(cls, value: int) -> int:
        """0 — общего дедлайна нет; отрицательное значение сломало бы бюджет."""
        if value < 0:
            raise ValueError("значение не может быть отрицательным")
        return value

    @field_validator(
        "llm_max_tokens",
        "agent_plan_timeout_s",
        "agent_step_timeout_s",
        "agent_step_fix_attempts",
        "agent_insight_timeout_s",
        "agent_dashboard_timeout_s",
        "agent_max_llm_calls",
        "agent_max_steps",
        "agent_max_parallel_llm",
        "agent_max_parallel_steps",
        "agent_max_replans",
        "agent_cache_ttl_s",
        "sql_timeout_s",
    )
    @classmethod
    def _positive_int(cls, value: int | None) -> int | None:
        """Лимиты и таймауты меньше 1 отключили бы этап или сломали бюджет."""
        if value is None:
            return None
        if value <= 0:
            raise ValueError("значение должно быть больше 0")
        return value


@lru_cache
def get_settings() -> Settings:
    """Кешированный доступ к настройкам."""
    return Settings()


def reload_settings() -> Settings:
    """Сбросить кэш настроек и перечитать .env (нужно после изменения переменных)."""
    get_settings.cache_clear()
    return get_settings()


settings = get_settings()
