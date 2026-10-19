"""CRUD сессий диалога.

Сессия — единица памяти (память уровня 1): session_id -> история
текстового диалога + история ResearchStep (см. core/memory/session_memory.py).
"""

import uuid

from fastapi import APIRouter

from app.api.schemas.chat import SessionCreate, SessionOut
from app.core.memory.session_memory import SessionMemory

router = APIRouter()

# Хранилище сессий (in-memory до подключения SessionMemory/SQLite).
_SESSIONS: dict[str, SessionOut] = {}


@router.post("", response_model=SessionOut)
async def create_session(payload: SessionCreate) -> SessionOut:
    """Создать новую сессию диалога (опц. с привязанным датасорсом)."""
    session_id = uuid.uuid4().hex
    session = SessionOut(session_id=session_id, datasource_id=payload.datasource_id)
    _SESSIONS[session_id] = session
    # Живая память сессии (SQLite): история диалога и шагов начинают сохраняться.
    SessionMemory(session_id)
    return session


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(session_id: str) -> SessionOut:
    """Получить метаданные сессии."""
    return _SESSIONS[session_id]  # KeyError -> 500; TODO: HTTPException(404)
