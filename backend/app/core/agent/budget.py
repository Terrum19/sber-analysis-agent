"""Бюджет одного хода агента: необязательный общий дедлайн и лимиты этапов.

Раньше каждый этап жил со своим таймаутом (LLM_TIMEOUT_S=600, повторы,
исправления плана и шагов), поэтому теоретический ход мог длиться
десятки минут. ``TurnBudget`` — единая точка, из которой берут таймауты
LLM, планировщик, SQL и инсайт; превышение поднимает ``BudgetExceeded``.
Общий дедлайн можно отключить (``total_s=None``): тогда ход идёт столько,
сколько нужно, а от зависаний защищают лимиты этапов и счётчики циклов.
"""

from __future__ import annotations

import math
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import Settings


class BudgetExceeded(RuntimeError):
    """Ход не уложился в бюджет: этап, остаток и причина."""

    def __init__(self, stage: str, remaining_s: float, detail: str = "") -> None:
        self.stage = stage
        self.remaining_s = remaining_s
        self.detail = detail
        if math.isfinite(remaining_s):
            message = f"{stage}: осталось {remaining_s:.1f} с"
        else:
            message = f"{stage}: без общего дедлайна"
        super().__init__(f"{message}; {detail}".strip() if detail else message)


@dataclass(slots=True)
class TurnBudget:
    """Бюджет хода: необязательный общий дедлайн и лимиты этапов.

    ``total_s=None`` — общего дедлайна нет: ход идёт столько, сколько нужно
    для качества. От зависаний защищают лимиты этапов (``stage_limits``),
    таймауты отдельных операций (LLM, шаг, инсайт, дашборд) и явные
    счётчики циклов (шаги плана, replan, исправления, toolgen, вызовы LLM).
    ``stage_limits`` — сколько секунд отведено этапу (planning, steps,
    insight, chart, dashboard). Для шага дополнительно действует
    ``timeout_for`` с per-step лимитом из настроек, поэтому фактический
    таймаут шага — минимум из трёх значений.
    """

    total_s: float | None = None
    stage_limits: dict[str, float] = field(default_factory=dict)
    max_llm_calls: int = 24
    max_steps: int = 8
    started_at: float = field(default_factory=time.monotonic)
    llm_calls: int = 0
    stage_started: dict[str, float] = field(default_factory=dict)
    stage_elapsed: dict[str, float] = field(default_factory=dict)

    def remaining(self) -> float:
        """Сколько секунд осталось до общего дедлайна; inf — дедлайна нет."""
        if not self.total_s:
            return math.inf
        return self.total_s - (time.monotonic() - self.started_at)

    def stage_remaining(self, stage: str) -> float:
        """Остаток этапа: минимум из общего остатка и лимита этапа."""
        left = self.remaining()
        limit = self.stage_limits.get(stage)
        if limit is None:
            return left
        started = self.stage_started.get(stage)
        if started is None:
            return min(left, limit)
        return min(left, limit - (time.monotonic() - started))

    def timeout_for(self, stage: str, default_s: float) -> float:
        """Таймаут запроса: не больше default_s, остатка хода и этапа."""
        return max(0.1, min(default_s, self.remaining(), self.stage_remaining(stage)))

    def consume_llm_call(self, stage: str) -> None:
        """Учесть вызов LLM; при исчерпании лимита — BudgetExceeded."""
        if self.llm_calls >= self.max_llm_calls:
            raise BudgetExceeded(
                stage, self.remaining(), f"лимит вызовов LLM {self.max_llm_calls} исчерпан"
            )
        if self.remaining() <= 0:
            raise BudgetExceeded(stage, self.remaining(), "истёк общий бюджет хода")
        stage_left = self.stage_remaining(stage)
        if stage_left <= 0:
            raise BudgetExceeded(stage, self.remaining(), "истёк бюджет этапа")
        self.llm_calls += 1

    def enter_stage(self, stage: str) -> None:
        """Отметить начало этапа (повторный вход сохраняет первый старт)."""
        self.stage_started.setdefault(stage, time.monotonic())

    def leave_stage(self, stage: str) -> None:
        """Отметить конец этапа и записать его длительность в метрики."""
        started = self.stage_started.pop(stage, None)
        if started is None:
            return
        elapsed = time.monotonic() - started
        self.stage_elapsed[stage] = self.stage_elapsed.get(stage, 0.0) + elapsed
        from app.core.agent.metrics import current_metrics

        metrics = current_metrics()
        if metrics is not None:
            metrics.record_stage(stage, elapsed)

    def snapshot(self) -> dict[str, float | int | None]:
        """Текущее состояние бюджета для логов и метрик."""
        remaining = self.remaining()
        return {
            "total_s": self.total_s,
            "remaining_s": None if math.isinf(remaining) else round(remaining, 3),
            "llm_calls": self.llm_calls,
            "max_llm_calls": self.max_llm_calls,
            "max_steps": self.max_steps,
        }


_budget: ContextVar[TurnBudget | None] = ContextVar("turn_budget", default=None)


def budget_from_settings(settings: Settings) -> TurnBudget:
    """Собрать бюджет хода из настроек приложения.

    ``AGENT_TURN_TIMEOUT_S=0`` — без общего дедлайна: качество важнее
    скорости, а от зависаний защищают таймауты этапов и явные счётчики
    циклов. При положительном значении этап ``steps`` получает остаток хода
    после резерва на планирование, инсайт, график и дашборд — иначе каждый
    шаг мог бы съесть весь дедлайн.
    """
    total_s = float(settings.agent_turn_timeout_s) if settings.agent_turn_timeout_s > 0 else None
    stage_limits: dict[str, float] = {
        "planning": float(settings.agent_plan_timeout_s),
        "insight": float(settings.agent_insight_timeout_s),
        "chart": 60.0,
        "dashboard": float(settings.agent_dashboard_timeout_s),
        "llm": float(settings.llm_timeout_s),
    }
    if total_s is not None:
        reserve = (
            settings.agent_plan_timeout_s
            + settings.agent_insight_timeout_s
            + settings.agent_dashboard_timeout_s
            + 60  # график: детерминированный, но синхронный SQL тоже должен влезть
        )
        stage_limits["steps"] = max(1.0, total_s - reserve)
    return TurnBudget(
        total_s=total_s,
        stage_limits=stage_limits,
        max_llm_calls=settings.agent_max_llm_calls,
        max_steps=settings.agent_max_steps,
    )


def current_budget() -> TurnBudget | None:
    """Бюджет текущего хода (None вне хода — тесты и утилиты)."""
    return _budget.get()


def set_budget(budget: TurnBudget | None) -> object:
    """Установить бюджет в контекст; вернуть токен для reset_budget."""
    return _budget.set(budget)


def reset_budget(token: object) -> None:
    """Снять бюджет, установленный set_budget."""
    _budget.reset(token)  # type: ignore[arg-type]
