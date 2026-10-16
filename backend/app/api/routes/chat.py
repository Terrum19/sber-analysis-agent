"""Диалоговый эндпоинт агента.

Отдаёт поток событий (SSE): текстовые чанки ответа, картинки по
готовности, ссылки на дашборды. Оркестрация — в core/agent/orchestrator.py.
"""

import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.schemas.chat import ChatRequest, ChatEvent

router = APIRouter()


@router.post("")
async def chat(payload: ChatRequest) -> StreamingResponse:
    """Один ход диалога: вопрос -> (текст, визуализации, дашборды).

    Запускает AgentOrchestrator.run() и стримит события через StreamingResponse.
    Каждый элемент — ChatEvent в формате SSE: data: {"type": "...", "data": {...}}
    """

    from app.core.agent.orchestrator import AgentOrchestrator

    orchestrator = AgentOrchestrator()

    async def event_stream():
        """Генерирует SSE-события от оркестратора."""
        async for event in orchestrator.run(payload):
            # Валидируем через ChatEvent и сериализуем в JSON
            chat_event = ChatEvent(**event)
            yield f"data: {chat_event.model_dump_json()}\n\n"

    STREAM_HEADERS = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
    }
    response = StreamingResponse(event_stream(), media_type="text/event-stream", headers=STREAM_HEADERS)
    return response
