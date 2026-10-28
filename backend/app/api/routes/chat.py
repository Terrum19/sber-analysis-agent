"""Диалоговый эндпоинт агента.

Отдаёт поток событий (SSE): текстовые чанки ответа, картинки по
готовности, ссылки на дашборды. Оркестрация — в core/agent/orchestrator.py.
"""

import asyncio
import concurrent.futures
import logging
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing, suppress
from typing import Annotated, TypeVar

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
T = TypeVar("T")
_END = object()


async def iterate_in_thread(source: Callable[[], AsyncIterator[T]]) -> AsyncIterator[T]:
    """Отдаёт элементы source() по мере готовности; source() идёт в своём потоке и loop.

    DuckDB вызывается синхронно и под одним db.lock (#189): в loop сервера
    такой вызов останавливал весь бэкенд — пинги SSE, /health, другие
    запросы. В своём loop блокируется только этот ход. Исключение source
    пробрасывается; потребитель ушёл — ход отменяется, выход ждёт его конца.
    """
    server = asyncio.get_running_loop()
    items: asyncio.Queue[tuple[object, BaseException | None]] = asyncio.Queue()
    loop = asyncio.new_event_loop()
    finished: concurrent.futures.Future[None] = concurrent.futures.Future()

    def send(item: object, error: BaseException | None = None) -> None:
        with suppress(RuntimeError):  # loop сервера уже закрыт
            server.call_soon_threadsafe(items.put_nowait, (item, error))

    async def pump() -> None:
        try:
            async with aclosing(source()) as stream:
                async for item in stream:
                    send(item)
        except BaseException as error:  # noqa: BLE001 — любой исход, и отмену, отдаём потребителю
            send(_END, error)
        else:
            send(_END)

    def work() -> None:
        asyncio.set_event_loop(loop)
        try:
            # Отменён до первого шага pump: потребитель ушёл, исход ему не нужен.
            with suppress(asyncio.CancelledError):
                loop.run_until_complete(task)
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            loop.close()
            finished.set_result(None)

    task = loop.create_task(pump())
    threading.Thread(target=work, name="chat-turn", daemon=True).start()
    try:
        while True:
            item, error = await items.get()
            if item is _END:
                if error is not None:
                    raise error
                return
            yield item  # type: ignore[misc]
    finally:
        with suppress(RuntimeError):  # loop хода уже закрыт
            loop.call_soon_threadsafe(task.cancel)
        await asyncio.wrap_future(finished)


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

    def turn() -> AsyncIterator[dict]:
        # Импорт (первый — около секунды) и оркестратор — в потоке хода:
        # loop сервера их не ждёт, LLM-клиенты оркестратора живут в loop хода.
        from app.core.agent.orchestrator import AgentOrchestrator

        return AgentOrchestrator(db=db).run(payload)

    async def event_stream():
        """Генерирует SSE-события от оркестратора."""
        try:
            async for event in iterate_in_thread(turn):
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
