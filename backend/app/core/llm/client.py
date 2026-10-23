"""Единый клиент к локальной LLM (OpenAI-совместимый протокол).

Замкнутый контур: по умолчанию Ollama/vLLM на localhost.
Менять провайдера — только через config.settings.llm_base_url/model.

Если переменная окружения USE_FAKE_LLM=true, конструктор возвращает
FakeLLM вместо реального клиента — для тестирования на CI без локальной модели.
"""

import json
import logging
import os
import re
import time
from typing import Any, TypeVar

import httpx
import instructor
import openai
from openai import AsyncOpenAI
from pydantic import BaseModel
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt

try:  # instructor >= 1.4
    from instructor.exceptions import InstructorRetryException
except ImportError:  # instructor 1.3.x
    from instructor.retry import InstructorRetryException

from app.config import settings
from app.core.llm import budget

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Сколько раз instructor переспрашивает модель, если ответ не прошёл валидацию схемы.
STRUCTURED_ATTEMPTS = 3
# Запрос длины контекста у сервера не должен задерживать ход.
SERVER_INFO_TIMEOUT_S = 5.0


def _validation_retries() -> AsyncRetrying:
    """Повторять только невалидный ответ модели.

    По умолчанию instructor повторяет любое исключение — и 401/404, и обрыв
    связи, которые клиент openai к тому же уже сам повторил (429, 5xx, сеть).
    """
    return AsyncRetrying(
        stop=stop_after_attempt(STRUCTURED_ATTEMPTS),
        retry=retry_if_exception_type(InstructorRetryException),
        reraise=True,
    )


def _is_fatal(exc: Exception) -> bool:
    """Ошибка, которую не исправит ни повтор, ни другой формат запроса.

    400/422 не считаем фатальными: модель или сервер могут не поддерживать
    tool_choice, и JSON-режим без tools тогда помогает.
    """
    if isinstance(exc, openai.APIConnectionError):  # включая таймаут
        return True
    return isinstance(exc, openai.APIStatusError) and exc.status_code not in (400, 422)


def _strip_code_fences(text: str) -> str:
    """Удалить markdown-кодовые блоки (```json ... ```) из текста.

    LLM часто оборачивает JSON в ```json ... ```, что ломает
    model_validate_json. Возвращаем чистый JSON-строку.
    """
    # Удаляем открывающий ```json или ``` и закрывающий ```
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE | re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)
    return text.strip()


class LLMClient:
    """Обёртка над AsyncOpenAI для локального инференса Qwen."""

    def __new__(cls) -> "LLMClient":  # noqa: PYI034 — в режиме USE_FAKE_LLM отдаёт FakeLLM
        if os.getenv("USE_FAKE_LLM", "false").lower() == "true":
            from app.core.llm.fake_client import FakeLLM

            return FakeLLM()
        return super().__new__(cls)

    def __init__(self) -> None:
        self._client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value() or "not-needed",
            timeout=settings.llm_timeout_s,
            # Хук видит каждый ответ, включая повторы instructor и fallback.
            http_client=openai.DefaultAsyncHttpxClient(
                event_hooks={"response": [self._check_context]}
            ),
        )
        self.model = settings.llm_model
        self.last_prompt_tokens: int | None = None
        self._instructor = instructor.from_openai(self._client)
        logger.info(
            "LLMClient инициализирован: base_url=%s, model=%s, timeout=%ss, temp=%s",
            settings.llm_base_url,
            self.model,
            settings.llm_timeout_s,
            settings.llm_temperature,
        )

    async def _check_context(self, response: httpx.Response) -> None:
        """Сверить prompt_tokens ответа с длиной промпта: не обрезал ли его сервер.

        При первом ответе спрашивает у сервера его контекст (модель к этому
        моменту загружена — Ollama показывает его в /api/ps). Сбой проверки
        запрос не роняет.
        """
        if response.status_code != 200 or not response.url.path.endswith("/chat/completions"):
            return
        try:
            await response.aread()
            prompt_tokens = (response.json().get("usage") or {}).get("prompt_tokens")
            messages = json.loads(response.request.content).get("messages") or []
            self.last_prompt_tokens = prompt_tokens
            if not prompt_tokens:
                return
            base_url = str(self._client.base_url)
            if not budget.known_server_context(base_url, self.model):
                ctx = await self._server_context()
                budget.remember_server_context(base_url, self.model, ctx)
            prompt_chars = sum(
                len(m["content"]) for m in messages if isinstance(m.get("content"), str)
            )
            ctx = budget.server_context(base_url, self.model) or settings.llm_num_ctx
            logger.debug("LLM промпт: %d символов, %d токенов", prompt_chars, prompt_tokens)
            reason = budget.check_prompt(prompt_chars, prompt_tokens, ctx)
        except Exception:  # проверка вспомогательная, запрос не роняет
            logger.warning("Проверка контекста LLM не удалась", exc_info=True)
            return
        if reason:
            budget.report_truncation(prompt_chars, prompt_tokens, reason)

    async def _server_context(self) -> int | None:
        """Контекст модели по словам сервера: vLLM — /v1/models, Ollama — /api/ps."""
        options: Any = {"timeout": SERVER_INFO_TIMEOUT_S, "max_retries": 0}

        async def fetch(path: str) -> Any:
            try:
                return await self._client.get(path, cast_to=object, options=options)
            except Exception:  # сервер может не знать такой путь
                logger.debug("LLM %s недоступен", path, exc_info=True)
                return None

        models = await fetch("models")
        ctx = budget.parse_server_context(models, None, self.model)
        root = str(self._client.base_url).rstrip("/")
        if ctx is None and root.endswith("/v1"):
            ps = await fetch(root.removesuffix("/v1") + "/api/ps")
            ctx = budget.parse_server_context(None, ps, self.model)
        return ctx

    async def complete(self, system: str, user: str, **kwargs: Any) -> str:
        """Одиночный completion (без structured output)."""
        logger.debug(
            ">>> complete() system=%r user=%r kwargs=%s",
            system,
            user,
            kwargs,
        )
        t0 = time.time()
        try:
            resp = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=settings.llm_temperature,
                **kwargs,
            )
        except Exception:
            logger.exception("LLM complete() ОШИБКА сетевого вызова")
            raise
        content = resp.choices[0].message.content or ""
        elapsed = time.time() - t0
        logger.debug("<<< complete() ответ за %.2fs: %r", elapsed, content)
        logger.info(
            "LLM complete(): модель=%s длина=%d время=%.2fs ответ=%r",
            self.model,
            len(content),
            elapsed,
            content,
        )
        return content

    async def complete_structured(self, system: str, user: str, schema: type[T]) -> T:
        """Structured output через Instructor (валидация по Pydantic-схеме).

        Использует instructor с tool_choice. При ошибке валидации делает
        fallback: список объектов, затем JSON-режим без tools.
        """
        schema_name = getattr(schema, "__name__", str(schema))
        t0 = time.time()
        try:
            result = await self._instructor.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=settings.llm_temperature,
                response_model=schema,
                max_retries=_validation_retries(),
            )
            elapsed = time.time() - t0
            logger.info(
                "<<< complete_structured() tools OK за %.2fs схему=%s результат=%r",
                elapsed,
                schema_name,
                result,
            )
            return result
        except Exception as e:
            if _is_fatal(e):
                raise
            logger.warning(
                "complete_structured() первичная попытка tools ОШИБКА: %s, идём в fallback",
                e,
            )

        # Fallback 1: список объектов схемы (иногда LLM присылает массив)
        logger.info("complete_structured() Fallback 1: list[%s]", schema_name)
        try:
            results = await self._instructor.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=settings.llm_temperature,
                response_model=list[schema],
                max_retries=_validation_retries(),
            )
            if results:
                elapsed = time.time() - t0
                logger.info(
                    "<<< complete_structured() fallback1 list OK за %.2fs первый=%r",
                    elapsed,
                    results[0],
                )
                return results[0]
        except Exception as e:
            if _is_fatal(e):
                raise
            logger.warning("complete_structured() fallback1 list() ОШИБКА", exc_info=True)

        # Fallback 2: JSON-режим без tools, парсим вручную
        logger.info("complete_structured() Fallback 2: JSON-режим без tools")
        raw = await self.complete(
            system=system,
            user=user + "\n\nОтветь только валидным JSON, соответствующим схеме модели.",
        )
        logger.debug("complete_structured() raw LLM ответ=%r", raw)

        # Пробуем несколько стратегий извлечения JSON
        candidates = []
        extracted = _extract_json_block(raw)
        if extracted:
            candidates.append(("extract_json_block", extracted))
        cleaned = _strip_code_fences(raw)
        if cleaned and cleaned != raw:
            candidates.append(("strip_code_fences", cleaned))
        candidates.append(("raw_as_is", raw))

        result = None
        for name, cand in candidates:
            logger.debug("complete_structured() пробуем стратегию %s: %r", name, cand[:200])
            try:
                result = schema.model_validate_json(cand)
                elapsed = time.time() - t0
                logger.info(
                    "<<< complete_structured() fallback2 JSON OK через %s за %.2fs результат=%r",
                    name,
                    elapsed,
                    result,
                )
                break
            except Exception as e:  # noqa: BLE001 — пробуем следующую стратегию
                logger.debug("complete_structured() стратегия %s не сработала: %s", name, e)
                continue

        if result is None:
            logger.error(
                "complete_structured() fallback2: не удалось извлечь валидный JSON из ответа модели"
            )
            logger.error("Сырой ответ модели: %r", raw)
            logger.error("Очищенный ответ: %r", cleaned)
            raise ValueError(
                f"LLM не вернул валидный JSON для схемы {schema_name}. Сырой ответ: {raw[:500]}..."
            )

        return result


def _extract_json_block(text: str) -> str | None:
    """Найти первый валидный JSON-объект в произвольном тексте.

    Перебирает все подстроки, начинающиеся с '{' и заканчивающиеся '}',
    и возвращает первую, которую удаётся распарсить через json.loads.
    Возвращает None, если подходящего блока нет.
    """
    import json as _json

    start = 0
    while True:
        open_idx = text.find("{", start)
        if open_idx == -1:
            return None
        depth = 0
        for i in range(open_idx, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[open_idx : i + 1]
                    try:
                        _json.loads(candidate)
                        return candidate
                    except (_json.JSONDecodeError, ValueError):
                        break
        start = open_idx + 1
