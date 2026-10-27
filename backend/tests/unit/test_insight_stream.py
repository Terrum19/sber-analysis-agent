"""Unit-тесты текстового стрима инсайта: парсер, prompt и InsightGenerator.stream."""

import asyncio

import pytest

from app.core.agent.insight import (
    InsightGenerator,
    _first_sentence,
    parse_insight_text,
)
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.fake_client import FakeLLM
from app.models.insight import Insight
from app.models.research_step import ResearchStep

FULL_TEXT = (
    "Выручка выросла на 10%\n- Рост в январе\n- Падение в феврале\nОграничения: только 2024 год"
)


@pytest.fixture
def db():
    manager = DuckDBManager(path=":memory:")
    manager.execute("CREATE TABLE temp_result_v1 AS SELECT 1 AS a UNION ALL SELECT 2 AS a")
    return manager


# --- parse_insight_text ---


def test_parse_full_text():
    insight = parse_insight_text(FULL_TEXT)
    assert insight.headline == "Выручка выросла на 10%"
    assert insight.details == ["Рост в январе", "Падение в феврале"]
    assert insight.limits == "только 2024 год"


def test_parse_without_limits():
    insight = parse_insight_text("Выручка выросла\n- Рост в январе")
    assert insight.headline == "Выручка выросла"
    assert insight.details == ["Рост в январе"]
    assert insight.limits == ""


def test_parse_without_bullets_keeps_lines_in_details():
    insight = parse_insight_text("Выручка выросла\nРост в январе\nПадение в феврале")
    assert insight.headline == "Выручка выросла"
    assert insight.details == ["Рост в январе", "Падение в феврале"]


def test_parse_strips_markdown_and_numbered_bullets():
    text = "```text\n# **Выручка** выросла\n• Рост в `январе`\n1. Падение в феврале\n```"
    insight = parse_insight_text(text)
    assert insight.headline == "Выручка выросла"
    assert insight.details == ["Рост в январе", "Падение в феврале"]


def test_parse_empty_text():
    insight = parse_insight_text("")
    assert insight == Insight(headline="", details=[], limits="")


def test_parse_bullet_with_limits_marker():
    insight = parse_insight_text("Вывод\n- Ограничения: только 2024 год")
    assert insight.headline == "Вывод"
    assert insight.details == []
    assert insight.limits == "только 2024 год"


def test_parse_continuation_joins_last_detail():
    insight = parse_insight_text("Выручка выросла\n- Рост в январе\nи феврале")
    assert insight.details == ["Рост в январе и феврале"]


def test_parse_multiline_limits_are_joined():
    insight = parse_insight_text(
        "Выручка выросла\nОграничения: только 2024 год\nнет данных за 2025"
    )
    assert insight.limits == "только 2024 год нет данных за 2025"


def test_parse_limits_case_and_dash():
    insight = parse_insight_text("Выручка выросла\nЛИМИТЫ — только 2024 год")
    assert insight.limits == "только 2024 год"


def test_first_sentence_variants():
    assert _first_sentence("Рост. Подробности") == "Рост."
    assert _first_sentence("Рост без точки") == "Рост без точки"
    assert _first_sentence("- Рост. Подробности") == "Рост."
    assert _first_sentence("") == ""


# --- InsightGenerator.stream ---


class ScriptedLLM(FakeLLM):
    """LLM с заданными дельтами/ошибкой и записанным промптом."""

    def __init__(self, chunks=None, error: Exception | None = None) -> None:
        super().__init__()
        self.chunks = chunks or []
        self.error = error
        self.prompt = ""
        self.fallback_calls = 0

    async def complete_stream(self, system, user, **kwargs):
        self.prompt = user
        for chunk in self.chunks:
            yield chunk
        if self.error is not None:
            raise self.error

    async def complete_structured(self, system, user, schema):
        self.fallback_calls += 1
        return Insight(headline="Фолбэк", details=["structured"], limits="")


def collect_stream(gen, **kwargs):
    emitted: list[str] = []

    async def _run():
        return await gen.stream(emit=emitted.append, **kwargs)

    return asyncio.run(_run()), emitted


def test_stream_emits_chunks_and_parses_insight(db):
    llm = ScriptedLLM(list(FULL_TEXT.splitlines(keepends=True)))
    insight, emitted = collect_stream(
        InsightGenerator(llm=llm, db=db), question="Выручка?", result_table="temp_result_v1"
    )

    assert "".join(emitted) == FULL_TEXT
    assert insight.headline == "Выручка выросла на 10%"
    assert insight.details == ["Рост в январе", "Падение в феврале"]
    assert insight.limits == "только 2024 год"
    assert llm.fallback_calls == 0


def test_stream_falls_back_on_error_before_first_delta(db):
    llm = ScriptedLLM(error=RuntimeError("LLM недоступна"))
    insight, emitted = collect_stream(
        InsightGenerator(llm=llm, db=db), question="Выручка?", result_table="temp_result_v1"
    )

    assert emitted == []
    assert llm.fallback_calls == 1
    assert insight.headline == "Фолбэк"


def test_stream_without_complete_stream_uses_generate(db):
    class NoStreamLLM:
        async def complete_structured(self, system, user, schema):
            return Insight(headline="Без стрима", details=[], limits="")

    emitted: list[str] = []

    async def _run():
        gen = InsightGenerator(llm=NoStreamLLM(), db=db)
        return await gen.stream("Выручка?", "temp_result_v1", emit=emitted.append)

    insight = asyncio.run(_run())
    assert emitted == []
    assert insight.headline == "Без стрима"


def test_stream_falls_back_on_empty_stream(db):
    llm = ScriptedLLM(chunks=[])
    insight, emitted = collect_stream(
        InsightGenerator(llm=llm, db=db), question="Выручка?", result_table="temp_result_v1"
    )

    assert emitted == []
    assert llm.fallback_calls == 1
    assert insight.headline == "Фолбэк"


def test_stream_raises_error_after_first_delta(db):
    llm = ScriptedLLM(chunks=["Начало "], error=RuntimeError("обрыв"))
    emitted: list[str] = []

    async def _run():
        gen = InsightGenerator(llm=llm, db=db)
        await gen.stream("Выручка?", "temp_result_v1", emit=emitted.append)

    with pytest.raises(RuntimeError, match="обрыв"):
        asyncio.run(_run())
    assert emitted == ["Начало "]
    assert llm.fallback_calls == 0


def test_stream_headline_falls_back_to_first_sentence(db):
    llm = ScriptedLLM(["- Выручка выросла. Подробности\n", "- Ещё деталь"])
    insight, _ = collect_stream(
        InsightGenerator(llm=llm, db=db), question="Выручка?", result_table="temp_result_v1"
    )

    assert insight.headline == "Выручка выросла."
    assert insight.details[0] == "Выручка выросла. Подробности"


def test_stream_prompt_is_plain_text(db):
    llm = ScriptedLLM([FULL_TEXT])
    collect_stream(
        InsightGenerator(llm=llm, db=db), question="Выручка?", result_table="temp_result_v1"
    )

    assert "Формат ответа — обычный текст" in llm.prompt
    assert "Ограничения:" in llm.prompt
    assert "Структура ответа" not in llm.prompt


def test_stream_prompt_includes_quality_fixes(db):
    llm = ScriptedLLM([FULL_TEXT])
    steps = [
        ResearchStep(
            step_id=0,
            tool="filter_rows",
            arguments={"column": "year", "operator": "=", "value": 2024},
            purpose="Только 2024 год",
        )
    ]
    collect_stream(
        InsightGenerator(llm=llm, db=db),
        question="Выручка?",
        result_table="temp_result_v1",
        steps=steps,
    )

    assert "Как получен результат" in llm.prompt
    assert "Только 2024 год" in llm.prompt
