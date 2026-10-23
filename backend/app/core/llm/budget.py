"""Длина контекста LLM: сколько промпта влезает и не обрезал ли его сервер.

Контекст задаётся на сервере (Ollama — OLLAMA_CONTEXT_LENGTH, vLLM —
--max-model-len); OpenAI-совместимый /v1 у Ollama options.num_ctx из тела
запроса игнорирует. Ollama при переполнении молча отрезает начало промпта, и
prompt_tokens в ответе упирается ровно в контекст сервера — по этому и ловим
обрезку. llm_num_ctx в конфиге — сколько контекста мы ждём; реальный контекст
клиент спрашивает у сервера после первого ответа, бюджет считается от меньшего.
"""

import logging
from contextvars import ContextVar
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# prompt_tokens не дальше этого от контекста сервера — промпт упёрся в контекст.
TRUNCATION_MARGIN_TOKENS = 16
# Вторичный признак из I-04: токенов меньше 70 % оценки «символы / 3».
# Делитель не llm_chars_per_token: тот занижен под кириллицу, а у кода и
# английского ~3,8 символа на токен — с ним признак срабатывал бы зря.
LOW_TOKENS_SHARE = 0.7
LOW_TOKENS_CHARS_PER_TOKEN = 3.0

# Реальный контекст сервера по (base_url, model); None — сервер его не сообщает.
_server_ctx: dict[tuple[str, str], int | None] = {}
# Обрезанные промпты текущего хода; список ставит оркестратор.
_truncations: ContextVar[list[dict] | None] = ContextVar("llm_truncations", default=None)


def estimate_tokens(chars: int) -> int:
    """Оценка токенов по символам (с запасом: кириллица — ~2,6 символа на токен)."""
    return int(chars / settings.llm_chars_per_token)


def _key(base_url: str | None, model: str | None) -> tuple[str, str]:
    return (base_url or settings.llm_base_url).rstrip("/"), model or settings.llm_model


def server_context(base_url: str | None = None, model: str | None = None) -> int | None:
    """Контекст, который сообщил сервер модели (по умолчанию — из конфига), или None."""
    return _server_ctx.get(_key(base_url, model))


def known_server_context(base_url: str, model: str) -> bool:
    return _key(base_url, model) in _server_ctx


def remember_server_context(base_url: str, model: str, ctx: int | None) -> None:
    _server_ctx[_key(base_url, model)] = ctx
    if ctx is None:
        logger.info(
            "Сервер LLM не сообщает длину контекста, берём LLM_NUM_CTX=%d", settings.llm_num_ctx
        )
    elif ctx < settings.llm_num_ctx:
        logger.warning(
            "Контекст сервера LLM %d токенов меньше LLM_NUM_CTX=%d: длинные промпты "
            "будут обрезаны. Ollama — OLLAMA_CONTEXT_LENGTH, vLLM — --max-model-len",
            ctx,
            settings.llm_num_ctx,
        )
    elif ctx > settings.llm_num_ctx:
        logger.info(
            "Контекст сервера LLM %d токенов, бюджет по LLM_NUM_CTX=%d", ctx, settings.llm_num_ctx
        )


def num_ctx() -> int:
    """Контекст для бюджета: меньшее из LLM_NUM_CTX и того, что сообщил сервер."""
    ctx = server_context()
    return min(settings.llm_num_ctx, ctx) if ctx else settings.llm_num_ctx


def prompt_budget(fixed: str, reserve_tokens: int) -> int:
    """Сколько символов остаётся на переменную часть промпта (колонки, контекст).

    fixed — неизменная часть (правила, описания тулов), reserve_tokens —
    запас на ответ модели. Не меньше нуля.
    """
    total_chars = (num_ctx() - reserve_tokens) * settings.llm_chars_per_token
    return max(0, int(total_chars) - len(fixed))


def parse_server_context(models: Any, ollama_ps: Any, model: str) -> int | None:
    """Контекст модели из /v1/models (vLLM, OpenRouter) или /api/ps (Ollama)."""
    for entry in (models or {}).get("data") or []:
        if entry.get("id") == model:
            ctx = entry.get("max_model_len") or entry.get("context_length")
            if ctx:
                return int(ctx)
    for entry in (ollama_ps or {}).get("models") or []:
        names = {entry.get("name"), entry.get("model")}
        if model in names or f"{model}:latest" in names:
            ctx = entry.get("context_length")
            if ctx:
                return int(ctx)
    return None


def check_prompt(prompt_chars: int, prompt_tokens: int | None, ctx: int) -> str | None:
    """Причина, по которой промпт похож на обрезанный сервером, или None.

    ctx — контекст сервера (или LLM_NUM_CTX, если сервер его не сообщает).
    """
    if not prompt_tokens:
        return None
    if prompt_tokens >= ctx - TRUNCATION_MARGIN_TOKENS:
        return f"prompt_tokens={prompt_tokens} упёрся в контекст сервера {ctx}"
    expected = prompt_chars / LOW_TOKENS_CHARS_PER_TOKEN
    if prompt_tokens < LOW_TOKENS_SHARE * expected:
        return f"prompt_tokens={prompt_tokens} меньше 70% оценки {int(expected)}"
    return None


def report_truncation(prompt_chars: int, prompt_tokens: int, reason: str) -> None:
    """Записать обрезку в лог и в список обрезок текущего хода."""
    logger.warning(
        "Промпт обрезан сервером LLM: %s (%d символов). Модель не видит начало промпта",
        reason,
        prompt_chars,
    )
    collected = _truncations.get()
    if collected is not None:
        collected.append(
            {"prompt_chars": prompt_chars, "prompt_tokens": prompt_tokens, "reason": reason}
        )


def track_truncations() -> list[dict]:
    """Начать сбор обрезок: вызовы LLM в этом контексте попадут в список."""
    collected: list[dict] = []
    _truncations.set(collected)
    return collected


PROBE_PASSWORD = "ЖИРАФ"
# Длина промпта проверки по умолчанию: как у промпта планировщика без колонок.
PROBE_CHARS = 12_000


async def probe_context(llm: Any, chars: int = PROBE_CHARS) -> dict:
    """Отправить промпт ~chars символов с паролем в начале и проверить, дошёл ли он целиком.

    Для check_llm и scripts/check_llm_client.py: печатают результат.
    """
    head = f"Пароль: {PROBE_PASSWORD}. Дальше описание колонок таблицы.\n"
    lines = []
    while len(head) + sum(map(len, lines)) < chars:
        lines.append(f"Колонка col_{len(lines)} — выручка магазина за день в рублях, без НДС.\n")
    system = head + "".join(lines)
    user = "Назови пароль из начала системного сообщения одним словом."
    answer = await llm.complete(system=system, user=user, max_tokens=16)
    prompt_chars = len(system) + len(user)
    tokens = getattr(llm, "last_prompt_tokens", None)
    ctx = server_context()
    return {
        "prompt_chars": prompt_chars,
        "prompt_tokens": tokens,
        "chars_per_token": round(prompt_chars / tokens, 2) if tokens else None,
        "server_ctx": ctx,
        "llm_num_ctx": settings.llm_num_ctx,
        "password_seen": PROBE_PASSWORD.lower() in answer.lower(),
        "truncated": check_prompt(prompt_chars, tokens, ctx or settings.llm_num_ctx),
    }
