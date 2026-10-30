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
from app.core.agent.budget import BudgetExceeded, current_budget
from app.core.agent.metrics import current_metrics
from app.core.llm import budget, ollama_setup
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
    if isinstance(exc, openai.APIConnectionError):  # включая таймаут
        return True
    return isinstance(exc, openai.APIStatusError) and exc.status_code not in (400, 422)


def _failure_kind(error: Exception) -> str:
    if isinstance(error, openai.APITimeoutError):
        return "таймаут"
    if isinstance(error, openai.APIConnectionError):
        return "соединение"
    if isinstance(error, openai.APIStatusError):
        return f"HTTP {error.status_code}"
    return type(error).__name__


def _schema_name(kwargs: dict[str, Any]) -> str:
    response_format = kwargs.get("response_format") or {}
    return (response_format.get("json_schema") or {}).get("name") or "-"


def _reasoning_tokens(response: Any) -> int | None:
    """Сколько токенов ушло на скрытые рассуждения (usage OpenRouter/OpenAI).

    None — провайдер не сообщил: у Ollama и vLLM такого поля нет.
    """
    usage = getattr(response, "usage", None)
    details = getattr(usage, "completion_tokens_details", None)
    value = getattr(details, "reasoning_tokens", None)
    return value if isinstance(value, int) else None


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
        cls, http_client: httpx.AsyncClient | None = None, **kwargs: Any
    ) -> "LLMClient":
        if os.getenv("USE_FAKE_LLM", "false").lower() == "true":
            from app.core.llm.fake_client import FakeLLM

            return FakeLLM()
        return super().__new__(cls)

    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        *,
        model: str | None = None,
        base_url: str | None = None,
        api_key: Any | None = None,
        timeout_s: int | None = None,
        max_tokens: int | None = None,
    ) -> None:
        """http_client — свой HTTP-клиент; бенчмарк так засекает 429 и таймауты.

        model/base_url/api_key/timeout_s — переопределения для пула моделей
        (P1); без них клиент берёт настройки приложения.
        """
        http_client = http_client or openai.DefaultAsyncHttpxClient()
        # Хук видит каждый ответ, включая повторы instructor и fallback.
        hooks = http_client.event_hooks
        http_client.event_hooks = {**hooks, "response": [*hooks["response"], self._check_context]}
        resolved_url = base_url or settings.llm_base_url
        resolved_key = (
            (api_key.get_secret_value() if hasattr(api_key, "get_secret_value") else api_key)
            or settings.llm_api_key.get_secret_value()
            or "not-needed"
        )
        self._client = AsyncOpenAI(
            base_url=resolved_url,
            api_key=resolved_key,
            timeout=timeout_s or settings.llm_timeout_s,
            max_retries=settings.llm_max_retries,
            http_client=http_client,
        )
        self.model = model or ollama_setup.active_model()
        self.max_tokens = max_tokens or settings.llm_max_tokens
        self.last_prompt_tokens: int | None = None
        # Текст последнего ответа json_schema: для диагностики ответов без нужных полей.
        self.last_raw: str | None = None
        self._instructor = instructor.from_openai(self._client)
        logger.debug(
            "LLMClient инициализирован: base_url=%s, model=%s, timeout=%ss, temp=%s",
            public_url(resolved_url),
            self.model,
            timeout_s or settings.llm_timeout_s,
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
        """Сверить prompt_tokens ответа с длиной промпта: не обрезал ли его сервер.

        Пока сервер не назвал свой контекст, спрашивает его после ответа (модель
        к этому моменту загружена — Ollama показывает его в /api/ps). В длину
        промпта входит и схема тулов от instructor: сервер считает её токены в
        prompt_tokens. Сбой проверки запрос не роняет.
        """
        if response.status_code != 200 or not response.url.path.endswith("/chat/completions"):
            return
        # SSE нельзя читать хуком: aread() забуферизует поток и отменит
        # инкрементальность вывода. Проверку контекста для стрима пропускаем.
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            return
        try:
            body = json.loads(response.request.content)
        except (TypeError, ValueError):
            body = {}
        if body.get("stream"):
            return
        try:
            await response.aread()
            prompt_tokens = (response.json().get("usage") or {}).get("prompt_tokens")
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
            async with asyncio.timeout(SERVER_INFO_TIMEOUT_S):
                await self._client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": "ok"}],
                    max_tokens=1,
                )
        except Exception as error:  # noqa: BLE001 — LLM может ещё не подняться
            logger.warning("LLM недоступна при старте, контекст сервера узнаем позже: %s", error)

    async def complete(self, system: str, user: str, **kwargs: Any) -> str:
        """Одиночный completion (без structured output).

        Таймаут — минимум из LLM_TIMEOUT_S и остатка бюджета хода; вызов
        учитывается в TurnMetrics. ``max_tokens`` по умолчанию ограничен,
        чтобы генерация не съела весь бюджет.

        Ответ с ``finish_reason=length`` повторяется один раз с лимитом
        LLM_LENGTH_RETRY_TOKENS: reasoning-модель (Qwen3 через OpenRouter)
        могла потратить весь max_tokens на скрытые рассуждения и вернуть
        пустой content. Обрыв по длине виден и у непустого ответа — он
        тоже повторяется, чтобы JSON не пришёл обрезанным.
        """
        logger.debug(
            ">>> complete() system=%r user=%r kwargs=%s",
            system,
            user,
            kwargs,
        )
        if settings.llm_reasoning_effort:
            effort = settings.llm_reasoning_effort
            reasoning = {"enabled": False} if effort == "none" else {"effort": effort}
            kwargs["extra_body"] = {**kwargs.get("extra_body", {}), "reasoning": reasoning}
        if settings.llm_provider:
            only = [name.strip() for name in settings.llm_provider.split(",") if name.strip()]
            kwargs["extra_body"] = {**kwargs.get("extra_body", {}), "provider": {"only": only}}
        budget = current_budget()
        timeout = (
            budget.timeout_for("llm", settings.llm_timeout_s) if budget else settings.llm_timeout_s
        )
        kwargs.setdefault("max_tokens", getattr(self, "max_tokens", settings.llm_max_tokens))
        kwargs.setdefault("timeout", timeout)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        retry_tokens = settings.llm_length_retry_tokens
        # Лучший непустой ответ: если повтор окажется пустым, вернём первый.
        best = ""
        while True:
            if budget is not None:
                budget.consume_llm_call("llm")
            started_at = time.perf_counter()
            try:
                async with asyncio.timeout(timeout):
                    resp = await self._client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        temperature=settings.llm_temperature,
                        **kwargs,
                    )
            except TimeoutError as error:
                # Таймаут этапа (бюджет), а не сетевой сбой: это BudgetExceeded,
                # чтобы планирование/инсайт деградировали по плану, а не как «ход завис».
                self._log_failure(error, time.perf_counter() - started_at)
                if budget is not None:
                    raise BudgetExceeded(
                        "llm", budget.remaining(), "LLM не ответила в бюджет"
                    ) from error
                raise
            except Exception as error:
                self._log_failure(error, time.perf_counter() - started_at)
                raise
            if not resp.choices:
                raise ValueError(f"LLM {self.model}: ответ без choices")
            choice = resp.choices[0]
            content = choice.message.content or ""
            finish_reason = getattr(choice, "finish_reason", None)
            reasoning_tokens = _reasoning_tokens(resp)
            if content and len(content) > len(best):
                best = content
            elapsed = time.perf_counter() - started_at
            prompt_tokens = resp.usage.prompt_tokens if resp.usage else 0
            completion_tokens = resp.usage.completion_tokens if resp.usage else 0
            metrics = current_metrics()
            if metrics is not None:
                metrics.record_llm(
                    prompt_tokens or 0, completion_tokens or 0, round(elapsed * 1000)
                )
            logger.debug("<<< complete() ответ за %.2fs: %r", elapsed, content)
            logger.info(
                "LLM %s: схема=%s за %.2f с, prompt_tokens=%s, ответ %d символов",
                self.model,
                _schema_name(kwargs),
                elapsed,
                prompt_tokens,
                len(content),
            )
            if finish_reason != "length":
                return content
            max_tokens = int(kwargs.get("max_tokens") or 0)
            logger.warning(
                "LLM %s: ответ обрезан по max_tokens=%s (finish_reason=length, "
                "reasoning_tokens=%s), видимых символов: %d",
                self.model,
                max_tokens,
                reasoning_tokens if reasoning_tokens is not None else "?",
                len(content),
            )
            # Больше лимита, чем позволяет контекст, просить нельзя: vLLM
            # ответит 400, Ollama обрежет промпт.
            room = max(0, settings.llm_num_ctx - prompt_tokens)
            retry_limit = min(retry_tokens, room) if retry_tokens else 0
            if retry_limit <= max_tokens:
                return best
            logger.info("LLM %s: повтор с max_tokens=%s", self.model, retry_limit)
            kwargs["max_tokens"] = retry_limit

    async def complete_stream(self, system: str, user: str, **kwargs: Any) -> AsyncIterator[str]:
        """Дельты текста из chat.completions.create(stream=True).

        ``async with stream`` важен для отмены: при закрытии генератора
        (aclose/CancelledError) AsyncStream.close() закрывает HTTP-ответ.
        """
        logger.debug(">>> complete_stream() system=%r user=%r", system, user)
        extra: dict[str, Any] = {}
        if settings.llm_max_tokens:
            extra["max_tokens"] = settings.llm_max_tokens
        extra.update(kwargs)

        t0 = time.time()
        first_at: float | None = None
        chunks = chars = 0
        try:
            stream = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=settings.llm_temperature,
                stream=True,
                **extra,
            )
            async with stream:
                async for chunk in stream:
                    if not chunk.choices:
                        continue  # usage-only чанк
                    choice = chunk.choices[0]
                    content = choice.delta.content if choice.delta else None
                    if not content:
                        if choice.finish_reason == "length":
                            logger.warning("LLM complete_stream(): ответ обрезан по max_tokens")
                        continue
                    if first_at is None:
                        first_at = time.time()
                        logger.info("LLM complete_stream(): первый чанк за %.2fs", first_at - t0)
                    chunks += 1
                    chars += len(content)
                    yield content
        except Exception:
            logger.exception("LLM complete_stream() ОШИБКА")
            raise
        logger.info(
            "LLM complete_stream(): чанков=%d символов=%d время=%.2fs",
            chunks,
            chars,
            time.time() - t0,
        )

    async def complete_structured(
        self,
        system: str,
        user: str,
        schema: type[T],
        *,
        schema_override: dict[str, Any] | None = None,
    ) -> T:
        """Structured output через Instructor (валидация по Pydantic-схеме).

        json_schema — грамматика на сервере; json_object — только JSON-режим;
        tools — instructor TOOLS с fallback-цепочкой.
        ``schema_override`` позволяет сузить схему (maxItems/enum) без
        изменения pydantic-модели и старых планов.
        """
        schema_name = getattr(schema, "__name__", str(schema))
        if settings.llm_structured_mode == "json_schema":
            return await self._complete_json_schema(system, user, schema, schema_override)
        if settings.llm_structured_mode == "json_object":
            return await self._complete_json_object(system, user, schema, schema_override)
        budget = current_budget()
        if budget is not None:
            budget.consume_llm_call("llm")
        timeout = (
            budget.timeout_for("llm", settings.llm_timeout_s) if budget else settings.llm_timeout_s
        )
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
                timeout=timeout,
                max_tokens=getattr(self, "max_tokens", settings.llm_max_tokens),
            )
            elapsed = time.time() - t0
            metrics = current_metrics()
            if metrics is not None:
                metrics.record_llm(
                    self.last_prompt_tokens or 0,
                    0,
                    round(elapsed * 1000),
                )
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

    async def _complete_json_schema(
        self,
        system: str,
        user: str,
        schema: type[T],
        schema_override: dict[str, Any] | None = None,
    ) -> T:
        """Один запрос с response_format=json_schema, толерантный разбор, один переспрос.

        Грамматика на стороне сервера не даёт модели уйти в прозу или в
        несколько tool_calls; оставшиеся огрехи (обёртки, список вместо
        объекта) чинит structured.parse_structured. Переспрос несёт текст
        ошибки — модели есть что исправлять, а префикс промпта тот же
        (KV-кеш Ollama/vLLM переиспользуется).
        """
        from app.core.llm.structured import StructuredParseError, parse_structured, repair_prompt

        json_schema = schema_override or self._schema_for(schema)
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "schema": json_schema},
        }
        extra: dict[str, Any] = {"response_format": response_format}
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

    async def _complete_json_object(
        self,
        system: str,
        user: str,
        schema: type[T],
        schema_override: dict[str, Any] | None = None,
    ) -> T:
        """JSON-режим без грамматики: ответ парсится тем же толерантным разбором."""
        from app.core.llm.structured import StructuredParseError, parse_structured, repair_prompt

        prompt = user
        error: Exception | None = None
        for attempt in range(2):
            raw = await self.complete(system, prompt, response_format={"type": "json_object"})
            try:
                return parse_structured(schema, raw)
            except StructuredParseError as exc:
                error = exc
                logger.warning("complete_structured() json_object попытка %d: %s", attempt + 1, exc)
                prompt = user + repair_prompt(raw, exc)
        raise ValueError(f"LLM не вернул валидный JSON для схемы {schema.__name__}: {error}")

    def _schema_for(self, schema: type[BaseModel]) -> dict[str, Any]:
        """JSON schema с лимитами, которые не должны ломать старые планы.

        Ограничения добавляются на уровне схемы запроса, а не в
        pydantic-модели: существующие тесты и сохранённые планы читаются
        как раньше.
        """
        from app.models.insight import Insight
        from app.models.research_step import ResearchPlan

        data = schema.model_json_schema()
        if schema is ResearchPlan:
            steps = data.get("properties", {}).get("steps")
            if isinstance(steps, dict):
                steps["maxItems"] = settings.agent_max_steps
            step_schema = (data.get("$defs") or {}).get("ResearchStep", {})
            tool_schema = (step_schema.get("properties") or {}).get("tool")
            if isinstance(tool_schema, dict):
                # Ленивый импорт: registry -> tools -> base -> llm.client.
                from app.core.tools.registry import tool_registry

                tool_schema["enum"] = [tool.name for tool in tool_registry.planner_tools()]
        if schema is Insight:
            properties = data.get("properties", {})
            if isinstance(properties.get("headline"), dict):
                properties["headline"]["maxLength"] = 300
            if isinstance(properties.get("details"), dict):
                properties["details"]["maxItems"] = 5
        return data


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
