"""CRUD сессий диалога.

Сессия — единица памяти (память уровня 1): session_id -> история
текстового диалога + история ResearchStep (см. core/memory/session_memory.py).
Сессия создаётся только с подключённой таблицей; сменить таблицу и/или
название можно через PATCH, история каждой таблицы хранится отдельно. Источник правды —
SQLite SessionMemory: сессии и их метаданные переживают перезапуск.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_db
from app.api.schemas.chat import SessionCreate, SessionOut, SessionUpdate
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory.session_memory import SessionMemory

router = APIRouter()

SESSION_LIST_LIMIT = 30


def table_columns(db: DuckDBManager, table: str) -> list[str]:
    """Колонки подключённой таблицы; 404, если такой таблицы нет."""
    columns = db.columns(table)
    if not columns:
        raise HTTPException(status_code=404, detail=f"Таблица «{table}» не найдена.")
    return columns


def session_out(memory: SessionMemory) -> SessionOut:
    return SessionOut(
        session_id=memory.session_id,
        table=memory.table,
        datasource_id=memory.datasource_id,
        title=memory.title,
        created_at=memory.created_at,
    )


@router.get("", response_model=list[SessionOut])
def list_sessions(offset: Annotated[int, Query(ge=0)] = 0) -> list[SessionOut]:
    """Сессии для сайдбара, недавно изменённые первыми, по SESSION_LIST_LIMIT с offset."""
    rows = SessionMemory.list_sessions(SESSION_LIST_LIMIT, offset)
    return [SessionOut(**row) for row in rows]


@router.post("", response_model=SessionOut)
def create_session(
    payload: SessionCreate, db: Annotated[DuckDBManager, Depends(get_db)]
) -> SessionOut:
    """Создать новую сессию диалога над подключённой таблицей."""
    columns = table_columns(db, payload.table)
    memory = SessionMemory(uuid.uuid4().hex, db)
    memory.set_meta(payload.title, payload.datasource_id)
    memory.set_table(payload.table, columns)
    return session_out(memory)


@router.patch("/{session_id}", response_model=SessionOut)
def update_session(
    session_id: str, payload: SessionUpdate, db: Annotated[DuckDBManager, Depends(get_db)]
) -> SessionOut:
    """Переключить сессию на другую таблицу и/или переименовать её.

    Неизвестная таблица — 404 до любых изменений: название тоже не меняется.
    """
    memory = SessionMemory(session_id, db)
    if not memory.exists():
        raise HTTPException(status_code=404, detail="Сессия не найдена.")
    if payload.table is not None:
        memory.set_table(payload.table, table_columns(db, payload.table))
    if payload.title is not None:
        memory.set_title(payload.title)
    return session_out(memory)


@router.get("/{session_id}", response_model=SessionOut)
def get_session(session_id: str) -> SessionOut:
    """Получить метаданные сессии."""
    memory = SessionMemory(session_id)
    if not memory.exists():
        raise HTTPException(status_code=404, detail="Сессия не найдена.")
    return session_out(memory)


@router.delete("/{session_id}")
def delete_session(session_id: str) -> dict[str, bool]:
    """Удалить сессию диалога."""
    deleted = SessionMemory.delete(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Сессия не найдена.")
    return {"ok": True}
