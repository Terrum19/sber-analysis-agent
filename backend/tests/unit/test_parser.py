"""Unit-тесты robust-парсера шагов ResearchStep.

LLM не соблюдает формат — парсер должен переживать любой вывод.
"""
import pytest

from app.core.agent.parser import parse_steps


@pytest.mark.parametrize(
    "raw",
    [
        '{"steps": [{"tool": "aggregate", "columns": ["x"]}]}',
        '```json\n{"steps": [{"tool": "correlation", "columns": ["a", "b"]}]}\n```',
        'Вот план:\n{"steps": [{"tool": "time_series", "columns": ["t", "m"]}]}',
    ],
)
def test_parse_steps_valid(raw: str) -> None:
    steps = parse_steps(raw)
    assert len(steps) == 1
    assert steps[0].tool in {"aggregate", "correlation", "time_series"}


def test_parse_steps_fallback() -> None:
    """Совсем мусорный вывод -> запасной шаг с кодогенерацией."""
    steps = parse_steps("вообще не json")
    assert steps[0].needs_llm_codegen is True
