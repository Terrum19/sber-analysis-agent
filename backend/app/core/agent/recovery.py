"""Обработка ошибок: ретрай предыдущего шага с обратной связью.

Стратегия: при сбое шага — повторить его с текстом ошибки в промпте
(самоисправление), затем эскалация пользователю с просьбой уточнить.
"""

import asyncio
import functools
from collections.abc import Callable
from typing import Any


async def with_retry(fn: Callable[..., Any], *args: Any, attempts: int = 3, **kwargs: Any) -> Any:
    """Выполнить fn с повторами и растущей паузой."""
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            await asyncio.sleep(2**attempt)
    raise last_exc  # type: ignore[misc]


def retryable(attempts: int = 3) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Декоратор: ретраи с экспоненциальной паузой."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await with_retry(fn, *args, attempts=attempts, **kwargs)

        return wrapper

    return decorator
