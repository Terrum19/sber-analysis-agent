"""Бюджет промпта и признаки обрезки (I-04)."""

import contextvars

import pytest

from app.config import settings
from app.core.llm import budget


@pytest.fixture(autouse=True)
def context(monkeypatch):
    monkeypatch.setattr(settings, "llm_base_url", "http://llm.test/v1")
    monkeypatch.setattr(settings, "llm_model", "m")
    monkeypatch.setattr(settings, "llm_num_ctx", 1000)
    monkeypatch.setattr(settings, "llm_chars_per_token", 2.5)
    monkeypatch.setattr(budget, "_server_ctx", {})
    monkeypatch.setattr(budget, "_server_ctx_asked", {})


def test_budget_is_counted_from_num_ctx() -> None:
    # (1000 - 200 токенов на ответ) × 2,5 символа − 500 символов неизменной части
    assert budget.prompt_budget("x" * 500, reserve_tokens=200) == 1500


def test_budget_follows_smaller_server_context() -> None:
    budget.remember_server_context("http://llm.test/v1/", "m", 600)
    assert budget.num_ctx() == 600
    assert budget.prompt_budget("x" * 500, reserve_tokens=200) == 500


def test_bigger_server_context_does_not_raise_budget() -> None:
    budget.remember_server_context("http://llm.test/v1", "m", 32768)
    assert budget.num_ctx() == 1000


def test_unknown_server_context_is_asked_again_later(monkeypatch) -> None:
    assert budget.should_ask_server_context("http://llm.test/v1", "m")
    budget.remember_server_context("http://llm.test/v1", "m", None)  # таймаут, перезапуск
    assert budget.server_context() is None
    assert not budget.should_ask_server_context("http://llm.test/v1", "m")  # не чаще раза в минуту

    monkeypatch.setattr(budget, "SERVER_CTX_RETRY_S", 0)
    assert budget.should_ask_server_context("http://llm.test/v1", "m")
    budget.remember_server_context("http://llm.test/v1", "m", 600)
    assert budget.num_ctx() == 600
    assert not budget.should_ask_server_context("http://llm.test/v1", "m")


def test_budget_is_never_negative() -> None:
    assert budget.prompt_budget("x" * 10_000, reserve_tokens=200) == 0


def test_server_context_from_vllm_and_ollama() -> None:
    vllm = {"data": [{"id": "other", "max_model_len": 1}, {"id": "m", "max_model_len": 8192}]}
    assert budget.parse_server_context(vllm, None, "m") == 8192
    ollama_v1 = {"data": [{"id": "m", "object": "model"}]}
    ollama_ps = {"models": [{"name": "m:latest", "model": "m:latest", "context_length": 4096}]}
    assert budget.parse_server_context(ollama_v1, None, "m") is None
    assert budget.parse_server_context(None, ollama_ps, "m") == 4096
    assert budget.parse_server_context(None, {"models": []}, "m") is None


@pytest.mark.parametrize(
    ("tokens", "ctx", "truncated"),
    [
        (4096, 4096, True),  # Ollama 0.24: упёрся в контекст сервера
        (4090, 4096, True),  # в пределах запаса от контекста
        (2050, 4096, True),  # Ollama 0.35.1: обрезанный промпт — около половины контекста
        (4600, 16384, False),  # кириллица: токенов больше, чем символов / 3
        (3200, 16384, False),  # код и английский: ~3,8 символа на токен
        (1500, 16384, True),  # намного меньше оценки
        (None, 4096, False),  # сервер не прислал usage
    ],
)
def test_check_prompt(tokens: int | None, ctx: int, truncated: bool) -> None:
    assert (budget.check_prompt(12_000, tokens, ctx) is not None) is truncated


def test_truncations_are_collected_only_when_tracked() -> None:
    def turn() -> list[dict]:
        budget.report_truncation(100, 10, "мало")  # вне хода — только лог
        collected = budget.track_truncations()
        budget.report_truncation(100, 10, "мало")
        return collected

    # Свой контекст: список хода не должен остаться другим тестам.
    collected = contextvars.copy_context().run(turn)
    assert collected == [{"prompt_chars": 100, "prompt_tokens": 10, "reason": "мало"}]
