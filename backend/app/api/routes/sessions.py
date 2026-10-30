"""CRUD сессий диалога.

Сессия — единица памяти (память уровня 1): session_id -> ходы диалога
(см. core/memory/session_memory.py). Таблица при создании необязательна;
PATCH меняет таблицу по умолчанию и/или название, ходы при этом остаются.
Воспоминания сессии (#197) — ``/{id}/memories``: список, добавление,
включение и выключение записи. ``/{id}/dashboard-ideas`` — варианты
дашборда по таблице сессии для кнопок в чате (#224). ``/{id}/bundle`` и
``/import`` — бандл сессии и импорт его в новую сессию (#249).
Источник правды — SQLite SessionMemory: сессии и их метаданные переживают
перезапуск.
"""

import asyncio
import json
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import get_db
from app.api.schemas.chat import (
    DashboardIdeasOut,
    DashboardIdeasRequest,
    MemoryCreate,
    MemoryOut,
    MemoryUpdate,
    SessionCreate,
    SessionOut,
    SessionUpdate,
)
from app.core.dashboards.ideas import generate_ideas
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory import session_bundle
from app.core.memory.session_memory import SessionMemory

logger = logging.getLogger(__name__)

router = APIRouter()

SESSION_LIST_LIMIT = 30
BUNDLE_MAX_BYTES = 10 << 20


def table_columns(db: DuckDBManager, table: str) -> list[str]:
    """Колонки подключённой таблицы; 404, если такой таблицы нет."""
    columns = db.columns(table)
    if not columns:
        raise HTTPException(status_code=404, detail=f"Таблица «{table}» не найдена.")
    return columns


def existing(session_id: str) -> SessionMemory:
    """Сохранённая сессия; 404, если её нет."""
    memory = SessionMemory(session_id)
    if not memory.exists():
        raise HTTPException(status_code=404, detail="Сессия не найдена.")
    return memory


def session_out(memory: SessionMemory) -> SessionOut:
    return SessionOut(
        session_id=memory.session_id,
        table=memory.table,
        datasource_id=memory.datasource_id,
        title=memory.title,
        created_at=memory.created_at,
        imported=memory.imported,
    )


@router.get("", response_model=list[SessionOut])
def list_sessions(offset: Annotated[int, Query(ge=0)] = 0) -> list[SessionOut]:
    """Сессии с ходами для сайдбара, недавно изменённые первыми, по SESSION_LIST_LIMIT с offset."""
    rows = SessionMemory.list_sessions(SESSION_LIST_LIMIT, offset)
    return [SessionOut(**row) for row in rows]


@router.post("", response_model=SessionOut)
def create_session(
    payload: SessionCreate, db: Annotated[DuckDBManager, Depends(get_db)]
) -> SessionOut:
    """Создать новую сессию диалога; таблица, если задана, становится таблицей по умолчанию."""
    if payload.table is not None:
        table_columns(db, payload.table)
    memory = SessionMemory(uuid.uuid4().hex, db)
    memory.set_meta(payload.title, payload.datasource_id)
    if payload.table is not None:
        memory.set_table(payload.table)
    return session_out(memory)


@router.post("/import", response_model=session_bundle.Imported)
async def import_session(request: Request) -> session_bundle.Imported:
    """Создать новую сессию из бандла; существующие сессии не меняются.

    Неизвестная версия формата и повреждённый бандл — 400 с текстом для
    пользователя, бандл больше BUNDLE_MAX_BYTES — 413. Ничего из бандла не
    выполняется.
    """
    body = await request.body()
    if len(body) > BUNDLE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Сессия слишком большая для импорта.")
    try:
        raw = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Это не сессия: данные не читаются.") from None
    try:
        return await asyncio.to_thread(session_bundle.load, raw)
    except session_bundle.BundleFormatError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


@router.patch("/{session_id}", response_model=SessionOut)
def update_session(
    session_id: str, payload: SessionUpdate, db: Annotated[DuckDBManager, Depends(get_db)]
) -> SessionOut:
    """Сменить таблицу по умолчанию и/или переименовать сессию.

    Неизвестная таблица — 404 до любых изменений: название тоже не меняется.
    """
    memory = SessionMemory(session_id, db)
    if not memory.exists():
        raise HTTPException(status_code=404, detail="Сессия не найдена.")
    if payload.table is not None:
        table_columns(db, payload.table)
        memory.set_table(payload.table)
    if payload.title is not None:
        memory.set_title(payload.title)
    return session_out(memory)


@router.get("/{session_id}", response_model=SessionOut)
def get_session(session_id: str) -> SessionOut:
    """Получить метаданные сессии."""
    return session_out(existing(session_id))


@router.get("/{session_id}/bundle")
def get_bundle(session_id: str) -> dict:
    """Бандл сессии без ленты: ленту добавляет фронт (#249). Пароля в нём нет."""
    try:
        return session_bundle.build(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Сессия не найдена.") from None


@router.get("/{session_id}/memories", response_model=list[MemoryOut])
def list_memories(session_id: str) -> list[MemoryOut]:
    """Все воспоминания сессии, включённые и выключенные, старые первыми."""
    return [MemoryOut(**item) for item in existing(session_id).memories(include_disabled=True)]


@router.post("/{session_id}/memories", response_model=MemoryOut, status_code=201)
def add_memory(session_id: str, payload: MemoryCreate) -> MemoryOut:
    """Добавить воспоминание; тот же текст не дублируется, а снова включается."""
    return MemoryOut(**existing(session_id).remember(payload.text))


@router.patch("/{session_id}/memories/{memory_id}", response_model=MemoryOut)
def update_memory(session_id: str, memory_id: str, payload: MemoryUpdate) -> MemoryOut:
    """Включить или выключить воспоминание: выключенное не идёт в контекст хода."""
    item = existing(session_id).set_memory_enabled(memory_id, payload.enabled)
    if item is None:
        raise HTTPException(status_code=404, detail="Воспоминание не найдено.")
    return MemoryOut(**item)


@router.post("/{session_id}/dashboard-ideas", response_model=DashboardIdeasOut)
def dashboard_ideas(
    session_id: str,
    db: Annotated[DuckDBManager, Depends(get_db)],
    body: DashboardIdeasRequest | None = None,
) -> DashboardIdeasOut:
    """4 варианта дашборда по таблице сессии; генерируются только по этому запросу.

    Тело с exclude — новые варианты, не повторяющие перечисленные.

    Синхронный обработчик идёт в пуле потоков: DuckDB и LLM не держат цикл сервера.
    """
    table = existing(session_id).table
    if table is None:
        raise HTTPException(status_code=409, detail="К сессии не подключена таблица.")
    try:
        ideas = asyncio.run(generate_ideas(db, table, exclude=body.exclude if body else ()))
    except Exception as exc:
        logger.exception("идеи дашборда для %s не построены", table)
        raise HTTPException(status_code=502, detail=f"Модель не ответила: {exc}") from exc
    return DashboardIdeasOut(ideas=ideas)


@router.delete("/{session_id}")
def delete_session(session_id: str) -> dict[str, bool]:
    """Удалить сессию диалога."""
    deleted = SessionMemory.delete(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Сессия не найдена.")
    return {"ok": True}
