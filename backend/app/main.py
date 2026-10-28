"""SberAnalyticalAgent — точка входа FastAPI-приложения.

Поднимает API (чат, дашборды, датасорсы), инициализирует сессии,
реестр тулов и WASM-песочницу при старте.

Запуск:  uvicorn app.main:app --reload --port 8000
"""

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.api.routes import artifacts, chat, dashboards, datasources, llm, metrics, sessions
from app.config import settings
from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.restore import restore_saved_sources
from app.core.data.result_retention import cleanup, cleanup_forever
from app.core.llm import ollama_setup
from app.core.llm.client import LLMClient
from app.core.logging_setup import configure_logging, startup_summary
from app.core.memory.profile_memory import ProfileMemory

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Инициализация ресурсов при старте (DuckDB, реестр, песочница), очистка temp_result_vN,
    сброс недоделанных описаний (pending → failed), переподключение источников БД, подготовка
    модели на сервере Ollama и запрос контекста сервера LLM."""
    log_file = configure_logging(settings.log_level, settings.storage_dir)
    logger.info("%s", startup_summary(settings, log_file))
    app.state.db = DuckDBManager(settings.duckdb_path)
    cleanup(app.state.db)
    interrupted = ProfileMemory(app.state.db).fail_pending()
    if interrupted:
        logger.warning("Перенос описания прерван рестартом: %s", ", ".join(interrupted))
    cleaner = asyncio.create_task(cleanup_forever(app.state.db))
    restorer = asyncio.create_task(restore_saved_sources(app.state.db))
    # Модель на сервере Ollama готовится в фоне (скачивание, контекст), затем
    # контекст сервера LLM — до первого вопроса: от него считается бюджет промпта.
    if settings.llm_prepare_model and os.getenv("USE_FAKE_LLM", "false").lower() != "true":
        warmer = asyncio.create_task(ollama_setup.prepare_forever())
    else:
        ollama_setup.mark_ready()
        warmer = asyncio.create_task(LLMClient().warm_up())
    yield
    for task in (cleaner, restorer, warmer):
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    app.state.db.close()


app = FastAPI(
    title="SberAnalyticalAgent",
    version="0.1.0",
    description="AI-Native BI-платформа: диалоговый агент над CSV/PostgreSQL/ClickHouse через DuckDB.",
    lifespan=lifespan,
)

app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(datasources.router, prefix="/api/datasources", tags=["datasources"])
app.include_router(dashboards.router, prefix="/api/dashboards", tags=["dashboards"])
app.include_router(artifacts.router, prefix="/api/artifacts", tags=["artifacts"])
app.include_router(llm.router, prefix="/api/llm", tags=["llm"])
app.include_router(metrics.router, prefix="/api/metrics", tags=["metrics"])


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Проверка живости сервиса."""
    return {"status": "ok"}
