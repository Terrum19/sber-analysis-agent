"""CRUD сессий диалога.

Сессия — единица памяти (память уровня 1): session_id -> история
текстового диалога + история ResearchStep (см. core/memory/session_memory.py).
Сессия создаётся только с подключённой таблицей; сменить таблицу можно
через PATCH, история каждой таблицы хранится отдельно.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_db
from app.api.schemas.chat import SessionCreate, SessionOut, SessionTable
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory.session_memory import SessionMemory

router = APIRouter()

# Хранилище сессий (in-memory до подключения SessionMemory/SQLite).
_SESSIONS: dict[str, SessionOut] = {}


def table_columns(db: DuckDBManager, table: str) -> list[str]:
    """Колонки подключённой таблицы; 404, если такой таблицы нет."""
    columns = db.columns(table)
    if not columns:
        raise HTTPException(status_code=404, detail=f"Таблица «{table}» не найдена.")
    return columns


@router.post("", response_model=SessionOut)
async def create_session(
    payload: SessionCreate, db: Annotated[DuckDBManager, Depends(get_db)]
) -> SessionOut:
    """Создать новую сессию диалога над подключённой таблицей."""
    columns = table_columns(db, payload.table)
    session_id = uuid.uuid4().hex
    session = SessionOut(
        session_id=session_id,
        table=payload.table,
        datasource_id=payload.datasource_id,
        title=payload.title,
    )
    _SESSIONS[session_id] = session
    SessionMemory(session_id, db).set_table(payload.table, columns)
    return session


@router.patch("/{session_id}", response_model=SessionOut)
async def set_session_table(
    session_id: str, payload: SessionTable, db: Annotated[DuckDBManager, Depends(get_db)]
) -> SessionOut:
    """Переключить сессию на другую таблицу."""
    memory = SessionMemory(session_id, db)
    if not memory.exists():
        raise HTTPException(status_code=404, detail="Сессия не найдена.")
    memory.set_table(payload.table, table_columns(db, payload.table))
    session = _SESSIONS.get(session_id) or SessionOut(session_id=session_id)
    session = session.model_copy(update={"table": payload.table})
    _SESSIONS[session_id] = session
    return session


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(session_id: str) -> SessionOut:
    """Получить метаданные сессии."""
    if session_id not in _SESSIONS:
        memory = SessionMemory(session_id)
        if memory.exists() and memory.table:
            session = SessionOut(session_id=session_id, table=memory.table)
            _SESSIONS[session_id] = session
            return session
        raise HTTPException(status_code=404, detail="Сессия не найдена.")
    return _SESSIONS[session_id]
