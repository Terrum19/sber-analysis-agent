"""Конфигурация P0: нормализация URL, json_object и проверка лимитов."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_chat_completions_url_is_normalized() -> None:
    settings = Settings(
        _env_file=None,
        llm_base_url="https://openrouter.ai/api/v1/chat/completions",
    )
    assert settings.llm_base_url == "https://openrouter.ai/api/v1"


def test_json_object_mode_is_supported() -> None:
    assert Settings(_env_file=None, llm_structured_mode="json_object").llm_structured_mode == (
        "json_object"
    )


@pytest.mark.parametrize(
    "field",
    [
        "agent_turn_timeout_s",
        "agent_plan_timeout_s",
        "agent_max_llm_calls",
        "agent_max_steps",
        "sql_timeout_s",
        "llm_max_tokens",
    ],
)
def test_limits_must_be_positive(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: 0})


def test_empty_role_urls_are_allowed() -> None:
    settings = Settings(_env_file=None, llm_base_url_fast="", llm_base_url_sql="")
    assert settings.llm_base_url_fast == ""
