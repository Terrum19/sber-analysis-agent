"""Диалоговый эндпоинт агента.

Отдаёт поток событий (SSE): текстовые чанки ответа, картинки по
готовности, ссылки на дашборды. Оркестрация — в core/agent/orchestrator.py.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.deps import get_db
from app.api.schemas.chat import ChatEvent, ChatRequest
from app.core.data.duckdb_manager import DuckDBManager

router = APIRouter()
logger = logging.getLogger(__name__)
STREAM_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}
# Прокси Next рвёт соединение после 30 с тишины (#168): пингуем чаще.
SSE_PING_INTERVAL_S = 15.0
SSE_PING = ": ping\n\n"


async def with_pings(chunks: AsyncIterator[str], interval: float) -> AsyncIterator[str]:
    """Отдаёт chunks как есть; если следующего нет interval секунд — SSE-комментарий.

    Ожидание по таймауту не отменяется: генератор продолжает работу, порядок
    чанков сохраняется. Ожидание отменяется, только когда клиент ушёл.
    """
    iterator = aiter(chunks)
    pending: asyncio.Future[str] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(iterator))
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                yield SSE_PING
                continue
            finished, pending = pending, None
            try:
                chunk = finished.result()
            except StopAsyncIteration:
                return
            yield chunk
    finally:
        if pending is not None:
            pending.cancel()


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

    async def event_stream():
        """Генерирует SSE-события от оркестратора."""
        try:
            async for event in orchestrator.run(payload):
                # Валидируем через ChatEvent и сериализуем в JSON
                chat_event = ChatEvent(**event)
                yield f"data: {chat_event.model_dump_json()}\n\n"
        except Exception as exc:
            # Сбой вне оркестратора: по контракту стрима за error идёт done
            logger.exception("Ошибка в event_stream")
            for chat_event in (
                ChatEvent(type="error", data={"message": str(exc)}),
                ChatEvent(type="done"),
            ):
                yield f"data: {chat_event.model_dump_json()}\n\n"

    response = StreamingResponse(
        with_pings(event_stream(), SSE_PING_INTERVAL_S),
        media_type="text/event-stream",
        headers=STREAM_HEADERS,
    )
    return response
