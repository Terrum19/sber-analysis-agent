"""Диалоговый эндпоинт агента.

Отдаёт поток событий (SSE): текстовые чанки ответа, картинки по
готовности, ссылки на дашборды. Оркестрация — в core/agent/orchestrator.py.
"""

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.schemas.chat import ChatRequest

router = APIRouter()
STREAM_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}


@router.post("")
async def chat(payload: ChatRequest) -> StreamingResponse:
    """Один ход диалога: вопрос -> (текст, визуализации, дашборды).

    TODO(round-2): запустить AgentOrchestrator.run(payload) и стримить
    события через StreamingResponse(media_type="text/event-stream").
    """

    async def event_stream():  # pragma: no cover - заглушка
        yield 'data: {"type": "text", "chunk": "TODO: orchestrator"}\n\n'

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=STREAM_HEADERS)
