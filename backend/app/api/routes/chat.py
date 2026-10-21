"""Диалоговый эндпоинт агента.

Отдаёт поток событий (SSE): текстовые чанки ответа, картинки по
готовности, ссылки на дашборды. Оркестрация — в core/agent/orchestrator.py.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.deps import get_db
from app.api.schemas.chat import ChatEvent, ChatRequest
from app.core.data.duckdb_manager import DuckDBManager

router = APIRouter()
STREAM_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}


@router.post("")
async def chat(
    payload: ChatRequest, db: Annotated[DuckDBManager, Depends(get_db)]
) -> StreamingResponse:
    """Один ход диалога: вопрос -> (текст, визуализации, дашборды).

    Запускает AgentOrchestrator.run() и стримит события через StreamingResponse.
    Каждый элемент — ChatEvent в формате SSE: data: {"type": "...", "data": {...}}
    """

    from app.core.agent.orchestrator import AgentOrchestrator

    orchestrator = AgentOrchestrator(db=db)

    import logging

    logger = logging.getLogger(__name__)

    async def event_stream():
        """Генерирует SSE-события от оркестратора."""
        try:
            async for event in orchestrator.run(payload):
                # Валидируем через ChatEvent и сериализуем в JSON
                chat_event = ChatEvent(**event)
                yield f"data: {chat_event.model_dump_json()}\n\n"
        except Exception as exc:
            logger.exception("Ошибка в event_stream")
            chat_event = ChatEvent(type="error", data={"message": str(exc)})
            yield f"data: {chat_event.model_dump_json()}\n\n"

    STREAM_HEADERS = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
    }
    response = StreamingResponse(
        event_stream(), media_type="text/event-stream", headers=STREAM_HEADERS
    )
    return response
