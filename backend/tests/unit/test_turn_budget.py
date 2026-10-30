"""Бюджет хода: дедлайн, лимиты этапов и вызовов LLM."""

import time

import pytest

from app.config import Settings
from app.core.agent.budget import (
    BudgetExceeded,
    TurnBudget,
    budget_from_settings,
    current_budget,
    reset_budget,
    set_budget,
)


def test_remaining_and_timeout_are_bounded_by_stage() -> None:
    budget = TurnBudget(total_s=10, stage_limits={"planning": 2}, max_llm_calls=3)
    budget.enter_stage("planning")
    assert 0 < budget.remaining() <= 10
    assert budget.stage_remaining("planning") <= 2
    assert budget.timeout_for("planning", 100) <= 2
    assert budget.timeout_for("unknown", 100) <= 10


def test_llm_calls_are_capped() -> None:
    budget = TurnBudget(total_s=10, max_llm_calls=2, stage_limits={"llm": 10})
    budget.consume_llm_call("llm")
    budget.consume_llm_call("llm")
    with pytest.raises(BudgetExceeded, match="лимит вызовов"):
        budget.consume_llm_call("llm")


def test_expired_stage_raises() -> None:
    budget = TurnBudget(total_s=10, stage_limits={"llm": 0.01})
    budget.enter_stage("llm")
    time.sleep(0.02)
    with pytest.raises(BudgetExceeded):
        budget.consume_llm_call("llm")


def test_contextvar_round_trip() -> None:
    assert current_budget() is None
    budget = TurnBudget(total_s=10)
    token = set_budget(budget)
    try:
        assert current_budget() is budget
    finally:
        reset_budget(token)
    assert current_budget() is None


def test_budget_from_settings_reserves_stages() -> None:
    settings = Settings(
        _env_file=None,
        agent_turn_timeout_s=870,
        agent_plan_timeout_s=240,
        agent_insight_timeout_s=90,
        agent_dashboard_timeout_s=120,
        agent_max_steps=8,
        agent_max_llm_calls=6,
        llm_timeout_s=600,
    )
    budget = budget_from_settings(settings)
    assert budget.total_s == 870
    assert budget.max_steps == 8
    assert budget.max_llm_calls == 6
    assert budget.stage_limits["planning"] == 240
    assert 0 < budget.stage_limits["steps"] < 870
    assert budget.snapshot()["remaining_s"] > 0


def test_leave_stage_records_metrics() -> None:
    from app.core.agent.metrics import TurnMetrics, reset_metrics, set_metrics

    budget = TurnBudget(total_s=10)
    metrics = TurnMetrics(session_id="s", turn_id="t", question="q")
    metrics_token = set_metrics(metrics)
    try:
        budget.enter_stage("planning")
        time.sleep(0.01)
        budget.leave_stage("planning")
    finally:
        reset_metrics(metrics_token)
    assert metrics.stages["planning"] >= 0.01
