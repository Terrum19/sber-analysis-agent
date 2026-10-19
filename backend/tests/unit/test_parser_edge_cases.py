"""Тесты граничных случаев парсера."""

from app.core.agent.parser import parse_steps


def test_parse_empty_string():
    steps = parse_steps("")
    assert len(steps) == 1
    assert steps[0].needs_llm_codegen is True


def test_parse_whitespace_only():
    steps = parse_steps("   \n\t ")
    assert len(steps) == 1
    assert steps[0].needs_llm_codegen is True


def test_parse_json_with_extra_text():
    steps = parse_steps('Вот план: {"steps": [{"tool": "aggregate", "columns": ["x"]}]}')
    assert len(steps) == 1
    assert steps[0].tool == "aggregate"


def test_parse_cyrillic_keys():
    steps = parse_steps('{"steps": [{"tool": "correlation", "columns": ["a", "b"]}]}')
    assert len(steps) == 1
    assert steps[0].tool == "correlation"
