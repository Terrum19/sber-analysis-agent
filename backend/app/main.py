"""SberAnalyticalAgent — точка входа FastAPI-приложения.

Поднимает API (чат, дашборды, датасорсы), инициализирует сессии,
реестр тулов и WASM-песочницу при старте.

Запуск:  uvicorn app.main:app --reload --port 8000
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.api.routes import artifacts, chat, dashboards, datasources, sessions
from app.config import settings
from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.restore import restore_saved_sources


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Инициализация ресурсов при старте (DuckDB, реестр, песочница), переподключение источников БД."""
    # TODO(round-2): инициализация ToolRegistry, WASMRuntime
    app.state.db = DuckDBManager(settings.duckdb_path)
    restorer = asyncio.create_task(restore_saved_sources(app.state.db))
    yield
    restorer.cancel()
    with suppress(asyncio.CancelledError):
        await restorer
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


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Проверка живости сервиса."""
    return {"status": "ok"}
