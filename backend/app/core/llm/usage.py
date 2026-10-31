"""Токены хода: сколько промпта и ответа ушло на вызовы LLM — всего и по местам хода.

Счётчик ставит оркестратор (track_usage), а пишет LLMClient: внутри хода
каждый вызов идёт стримом, и каждый пришедший кусок (текст или рассуждения
модели) — tick(), а usage в конце — finish(). Место вызова (планирование,
шаг, плитка дашборда, вывод) задаёт llm_scope() вокруг кода, который зовёт LLM.

По каждому месту счётчик отдаёт в sink событие usage: пока вызов идёт — с
оценкой по числу кусков (не чаще TICK_INTERVAL_S), в конце — с точными
числами. Итог хода уходит фронту в done.
"""

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

# Как часто слать оценку токенов места, пока вызов LLM идёт.
TICK_INTERVAL_S = 0.3


@dataclass
class ScopeUsage:
    """Токены одного места хода: точные — по законченным вызовам, live — куски идущих."""

    fields: dict[str, Any]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    live: int = 0
    running: int = 0
    sent_at: float = 0.0

    def event(self) -> dict:
        return {
            "type": "usage",
            "data": {
                **self.fields,
                "key": scope_key(self.fields),
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens + self.live,
                "live": self.running > 0,
            },
        }


@dataclass
class TurnUsage:
    """Сумма usage по всем вызовам LLM хода и по местам хода."""

    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    sink: Callable[[dict], None] | None = None
    scopes: dict[str, ScopeUsage] = field(default_factory=dict)

    def as_event(self) -> dict[str, int]:
        return {
            "llm_calls": self.llm_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
        }

    def _scope(self) -> ScopeUsage:
        fields = _scope.get() or {}
        key = scope_key(fields)
        if key not in self.scopes:
            self.scopes[key] = ScopeUsage(dict(fields))
        return self.scopes[key]

    def _send(self, scope: ScopeUsage) -> None:
        scope.sent_at = time.monotonic()
        if self.sink is not None:
            self.sink(scope.event())


@dataclass
class LlmCall:
    """Один вызов LLM внутри хода: куски считаются в место, где он начался."""

    turn: TurnUsage
    scope: ScopeUsage
    ticks: int = 0

    def tick(self) -> None:
        self.ticks += 1
        self.scope.live += 1
        if time.monotonic() - self.scope.sent_at >= TICK_INTERVAL_S:
            self.turn._send(self.scope)

    def finish(self, usage: Any) -> None:
        """usage ответа (dict или объект openai; None — сервер не прислал): оценку — на точное."""
        prompt, completion = _numbers(usage)
        if usage is None:
            completion = self.ticks
        self.scope.live -= self.ticks
        self.scope.running -= 1
        self.scope.prompt_tokens += prompt
        self.scope.completion_tokens += completion
        self.turn.llm_calls += 1
        self.turn.prompt_tokens += prompt
        self.turn.completion_tokens += completion
        self.turn._send(self.scope)


# Счётчик текущего хода: задачи хода копируют контекст и пишут в тот же объект.
_usage: ContextVar[TurnUsage | None] = ContextVar("llm_usage", default=None)
# Место хода, где сейчас зовут LLM: stage, step_id, tile_id.
_scope: ContextVar[dict[str, Any] | None] = ContextVar("llm_scope", default=None)


def scope_key(fields: dict[str, Any]) -> str:
    """«planning», «step:3», «tile:2/step:5», «repair/planning»: так фронт находит строку прогресса.

    phase — часть хода со своим прогрессом («Проверка»: review, repair), идёт первой.
    """
    parts = []
    if fields.get("phase"):
        parts.append(str(fields["phase"]))
    if fields.get("tile_id") is not None:
        parts.append(f"tile:{fields['tile_id']}")
    if fields.get("step_id") is not None:
        parts.append(f"step:{fields['step_id']}")
    elif fields.get("stage"):
        parts.append(str(fields["stage"]))
    return "/".join(parts) or "turn"


@contextmanager
def llm_scope(**fields: Any) -> Iterator[None]:
    """Вызовы LLM внутри — в это место хода; поля дополняют внешнее место (плитка -> её шаг).

    step_id без stage — шаг; stage без step_id снимает шаг внешнего места.
    """
    merged = {**(_scope.get() or {}), **fields}
    if "stage" in fields and "step_id" not in fields:
        merged.pop("step_id", None)
    token = _scope.set({key: value for key, value in merged.items() if value is not None})
    try:
        yield
    finally:
        # Место вокруг yield генератора: закрыт в другом контексте — сбрасывать нечего.
        with suppress(ValueError):
            _scope.reset(token)


def track_usage(sink: Callable[[dict], None] | None = None) -> TurnUsage:
    """Начать счёт: вызовы LLM в этом контексте попадут в счётчик, события мест — в sink."""
    usage = TurnUsage(sink=sink)
    _usage.set(usage)
    _scope.set(None)
    return usage


def current_usage() -> TurnUsage | None:
    return _usage.get()


def start_call() -> LlmCall | None:
    """Начать вызов LLM в текущем месте хода; вне хода — None."""
    turn = _usage.get()
    if turn is None:
        return None
    scope = turn._scope()
    scope.running += 1
    return LlmCall(turn, scope)


def record_usage(usage: Any) -> None:
    """Вызов без стрима (хук ответа, повторы instructor): сразу точные числа."""
    if (call := start_call()) is not None:
        call.finish(usage or {})


def _numbers(usage: Any) -> tuple[int, int]:
    if usage is None:
        return 0, 0
    if isinstance(usage, dict):
        return usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0
    return getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0
