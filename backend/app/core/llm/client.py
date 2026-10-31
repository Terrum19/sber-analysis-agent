"""Единый клиент к локальной LLM (OpenAI-совместимый протокол).

Замкнутый контур: по умолчанию Ollama/vLLM на localhost.
Менять провайдера — только через config.settings.llm_base_url/model.

Если переменная окружения USE_FAKE_LLM=true, конструктор возвращает
FakeLLM вместо реального клиента — для тестирования на CI без локальной модели.
"""

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import AsyncIterator
from contextlib import aclosing
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
from app.core.llm import budget, ollama_setup
from app.core.llm.usage import current_usage, record_usage, start_call
from app.core.logging_setup import public_url

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
    if isinstance(exc, openai.APIConnectionError | TimeoutError):  # включая таймаут
        return True
    return isinstance(exc, openai.APIStatusError) and exc.status_code not in (400, 422)


def _failure_kind(error: Exception) -> str:
    if isinstance(error, openai.APITimeoutError | TimeoutError):
        return "таймаут"
    if isinstance(error, openai.APIConnectionError):
        return "соединение"
    if isinstance(error, openai.APIStatusError):
        return f"HTTP {error.status_code}"
    return type(error).__name__


def _schema_name(kwargs: dict[str, Any]) -> str:
    response_format = kwargs.get("response_format") or {}
    return (response_format.get("json_schema") or {}).get("name") or "-"


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

    def __new__(  # noqa: PYI034 — в режиме USE_FAKE_LLM отдаёт FakeLLM
        cls, http_client: httpx.AsyncClient | None = None
    ) -> "LLMClient":
        if os.getenv("USE_FAKE_LLM", "false").lower() == "true":
            from app.core.llm.fake_client import FakeLLM

            return FakeLLM()
        return super().__new__(cls)

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        """http_client — свой HTTP-клиент; бенчмарк так засекает 429 и таймауты."""
        http_client = http_client or openai.DefaultAsyncHttpxClient()
        # Хук видит каждый ответ, включая повторы instructor и fallback.
        hooks = http_client.event_hooks
        http_client.event_hooks = {**hooks, "response": [*hooks["response"], self._check_context]}
        self._client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value() or "not-needed",
            timeout=settings.llm_timeout_s,
            max_retries=settings.llm_max_retries,
            http_client=http_client,
        )
        self.model = ollama_setup.active_model()
        self.last_prompt_tokens: int | None = None
        # Текст последнего ответа json_schema: для диагностики ответов без нужных полей.
        self.last_raw: str | None = None
        self._instructor = instructor.from_openai(self._client)
        logger.debug(
            "LLMClient инициализирован: base_url=%s, model=%s, timeout=%ss, temp=%s",
            public_url(settings.llm_base_url),
            self.model,
            settings.llm_timeout_s,
            settings.llm_temperature,
        )

    def _log_failure(self, error: Exception, elapsed: float) -> None:
        logger.error(
            "LLM %s: сбой (%s) %s, ждали %.1f с: %s",
            self.model,
            _failure_kind(error),
            public_url(str(self._client.base_url)),
            elapsed,
            error,
        )
        logger.debug("LLM %s: трассировка сбоя", self.model, exc_info=error)

    async def _check_context(self, response: httpx.Response) -> None:
        """Хук ответа: usage — в токены хода, prompt_tokens — в проверку обрезки.

        Стрим (text/event-stream) не трогаем: aread() дождался бы конца ответа
        и стрим пришёл бы одним куском. Его usage и проверку делает _stream().
        """
        if response.status_code != 200 or not response.url.path.endswith("/chat/completions"):
            return
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            return
        try:
            await response.aread()
            usage = response.json().get("usage") or {}
            record_usage(usage)
            body = json.loads(response.request.content)
        except Exception:  # проверка вспомогательная, запрос не роняет
            logger.warning("Проверка контекста LLM не удалась", exc_info=True)
            return
        await self._check_prompt(body, usage.get("prompt_tokens"))

    async def _check_prompt(self, body: dict, prompt_tokens: int | None) -> None:
        """Сверить prompt_tokens ответа с длиной промпта: не обрезал ли его сервер.

        Пока сервер не назвал свой контекст, спрашивает его после ответа (модель
        к этому моменту загружена — Ollama показывает его в /api/ps). В длину
        промпта входит и схема тулов от instructor: сервер считает её токены в
        prompt_tokens. Сбой проверки запрос не роняет.
        """
        try:
            self.last_prompt_tokens = prompt_tokens
            if not prompt_tokens:
                return
            base_url = str(self._client.base_url)
            if budget.should_ask_server_context(base_url, self.model):
                ctx = await self._server_context()
                budget.remember_server_context(base_url, self.model, ctx)
            prompt_chars = sum(
                len(m["content"])
                for m in body.get("messages") or []
                if isinstance(m.get("content"), str)
            )
            if body.get("tools"):
                prompt_chars += len(json.dumps(body["tools"], ensure_ascii=False))
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

    async def warm_up(self) -> None:
        """Короткий запрос при старте бэкенда, чтобы узнать контекст сервера заранее.

        Иначе бюджет первого промпта (планировщик) считался бы от LLM_NUM_CTX, а
        не от реального контекста. Ollama показывает контекст только загруженной
        модели, поэтому сначала запрос, а контекст спрашивает хук ответа.
        Недоступная LLM старт не роняет: контекст узнаем при первом ответе.
        """
        if budget.server_context(str(self._client.base_url), self.model) is not None:
            return
        try:
            await self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "ok"}],
                max_tokens=1,
            )
        except Exception as error:  # noqa: BLE001 — LLM может ещё не подняться
            logger.warning("LLM недоступна при старте, контекст сервера узнаем позже: %s", error)

    async def complete(self, system: str, user: str, **kwargs: Any) -> str:
        """Одиночный completion (без structured output).

        Внутри хода (track_usage) — стримом: ответ тот же, но фронт видит
        токены вызова по мере генерации. Вне хода (eval, проверки) и при
        LLM_STREAM_CALLS=false — одним ответом, как раньше: токены тогда
        считает хук ответа.
        """
        if settings.llm_stream_calls and current_usage() is not None:
            return "".join([delta async for delta in self._stream(system, user, kwargs)])
        logger.debug(
            ">>> complete() system=%r user=%r kwargs=%s",
            system,
            user,
            kwargs,
        )
        started_at = time.perf_counter()
        try:
            # Срок на весь ответ, а не на паузу между байтами (timeout клиента):
            # OpenRouter, пока модель думает, шлёт пробелы, и запрос висел без конца.
            resp = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=settings.llm_temperature,
                    **kwargs,
                ),
                settings.llm_timeout_s,
            )
        except Exception as error:
            self._log_failure(error, time.perf_counter() - started_at)
            raise
        content = resp.choices[0].message.content or ""
        elapsed = time.perf_counter() - started_at
        logger.debug("<<< complete() ответ за %.2fs: %r", elapsed, content)
        logger.info(
            "LLM %s: схема=%s за %.2f с, prompt_tokens=%s, ответ %d символов",
            self.model,
            _schema_name(kwargs),
            elapsed,
            resp.usage.prompt_tokens if resp.usage else None,
            len(content),
        )
        return content

    async def complete_stream(self, system: str, user: str, **kwargs: Any) -> AsyncIterator[str]:
        """Текст ответа по кусочкам — для вывода, который фронт печатает сразу.

        Закрытие генератора (клиент ушёл, ход отменён) закрывает и HTTP-ответ.
        """
        # aclosing: закрытие этого генератора сразу закрывает и _stream с HTTP-ответом.
        async with aclosing(self._stream(system, user, kwargs)) as deltas:
            async for delta in deltas:
                yield delta

    async def _stream(self, system: str, user: str, kwargs: dict[str, Any]) -> AsyncIterator[str]:
        """Ответ стримом (stream=True): куски текста, а куски текста и рассуждений — в токены хода.

        Срок — как у complete(), на весь ответ. usage приходит последним
        чанком (stream_options.include_usage): оценку места хода по кускам
        он заменяет точными числами и идёт в проверку обрезки промпта.
        """
        logger.debug(">>> stream system=%r user=%r kwargs=%s", system, user, kwargs)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        call = start_call()
        started_at = time.perf_counter()
        first_at: float | None = None
        chars = 0
        usage = None
        try:
            async with asyncio.timeout(settings.llm_timeout_s):
                stream = await self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=settings.llm_temperature,
                    stream=True,
                    stream_options={"include_usage": True},
                    **kwargs,
                )
                async with stream:
                    async for chunk in stream:
                        usage = chunk.usage or usage
                        if not chunk.choices:
                            continue
                        delta = chunk.choices[0].delta
                        # Рассуждения (OpenRouter — reasoning, vLLM — reasoning_content)
                        # не показываем, но считаем: модель работает и тратит токены.
                        thinking = getattr(delta, "reasoning", None) or getattr(
                            delta, "reasoning_content", None
                        )
                        if (delta.content or thinking) and call is not None:
                            call.tick()
                        if not delta.content:
                            continue
                        if first_at is None:
                            first_at = time.perf_counter()
                        chars += len(delta.content)
                        yield delta.content
        except Exception as error:
            self._log_failure(error, time.perf_counter() - started_at)
            raise
        finally:
            if call is not None:
                call.finish(usage)
        prompt_tokens = usage.prompt_tokens if usage else None
        logger.info(
            "LLM %s: схема=%s стримом за %.2f с (первый кусок через %.2f с), "
            "prompt_tokens=%s, ответ %d символов",
            self.model,
            _schema_name(kwargs),
            time.perf_counter() - started_at,
            (first_at or time.perf_counter()) - started_at,
            prompt_tokens,
            chars,
        )
        await self._check_prompt({"messages": messages}, prompt_tokens)

    async def complete_structured(self, system: str, user: str, schema: type[T]) -> T:
        """Structured output через Instructor (валидация по Pydantic-схеме).

        Использует instructor с tool_choice. При ошибке валидации делает
        fallback: список объектов, затем JSON-режим без tools.
        """
        schema_name = getattr(schema, "__name__", str(schema))
        if settings.llm_structured_mode == "json_schema":
            return await self._complete_json_schema(system, user, schema)
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
                "LLM %s: схема=%s (tools) за %.2f с, prompt_tokens=%s",
                self.model,
                schema_name,
                elapsed,
                self.last_prompt_tokens,
            )
            logger.debug("<<< complete_structured() tools результат=%r", result)
            return result
        except Exception as e:
            if _is_fatal(e):
                self._log_failure(e, time.time() - t0)
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
                logger.debug(
                    "<<< complete_structured() fallback1 list OK за %.2fs первый=%r",
                    elapsed,
                    results[0],
                )
                return results[0]
        except Exception as e:
            if _is_fatal(e):
                self._log_failure(e, time.time() - t0)
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
                logger.debug(
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
            logger.debug("Сырой ответ модели: %r", raw)
            logger.debug("Очищенный ответ: %r", cleaned)
            raise ValueError(
                f"LLM не вернул валидный JSON для схемы {schema_name}. Сырой ответ: {raw[:500]}..."
            )

        return result

    async def _complete_json_schema(self, system: str, user: str, schema: type[T]) -> T:
        """Один запрос с response_format=json_schema, толерантный разбор, один переспрос.

        Грамматика на стороне сервера не даёт модели уйти в прозу или в
        несколько tool_calls; оставшиеся огрехи (обёртки, список вместо
        объекта) чинит structured.parse_structured. Переспрос несёт текст
        ошибки — модели есть что исправлять, а префикс промпта тот же
        (KV-кеш Ollama переиспользуется).
        """
        from app.core.llm.structured import StructuredParseError, parse_structured, repair_prompt

        response_format = {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
        }
        extra: dict[str, Any] = {"response_format": response_format}
        if settings.llm_max_tokens:
            extra["max_tokens"] = settings.llm_max_tokens
        prompt = user
        error: Exception | None = None
        for attempt in range(2):
            t0 = time.time()
            raw = await self.complete(system, prompt, **extra)
            self.last_raw = raw
            try:
                result = parse_structured(schema, raw)
            except StructuredParseError as exc:
                error = exc
                logger.warning("complete_structured() json_schema попытка %d: %s", attempt + 1, exc)
                prompt = user + repair_prompt(raw, exc)
                continue
            logger.debug(
                "<<< complete_structured() json_schema OK за %.2fs схему=%s",
                time.time() - t0,
                schema.__name__,
            )
            return result
        raise ValueError(f"LLM не вернул валидный JSON для схемы {schema.__name__}: {error}")


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
