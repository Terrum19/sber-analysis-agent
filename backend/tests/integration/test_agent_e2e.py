"""Integration-тест: CSV -> DuckDB -> план -> выполнение -> инсайт.

Сценарий №1-2 чекпоинта: осмысленное описание данных и ответы,
опирающиеся на эти данные.
"""

import pytest


@pytest.mark.skip(reason="TODO(round-2): требует мок LLM или локальный Qwen")
async def test_full_research_flow() -> None:
    assert True
