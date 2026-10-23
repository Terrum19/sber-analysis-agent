"""Толерантный разбор structured-ответа LLM (app/core/llm/structured.py)."""

import json

import pytest

from app.core.llm.structured import (
    StructuredParseError,
    loads_lenient,
    merge_tool_calls,
    parse_structured,
)
from app.models.insight import Insight
from app.models.research_step import ResearchPlan

STEP = {"tool": "group_by", "arguments": {"by": ["region"], "column": "amount", "function": "SUM"}}


@pytest.mark.parametrize(
    "text",
    [
        json.dumps({"steps": [STEP]}),
        "Вот план:\n```json\n" + json.dumps({"steps": [STEP]}) + "\n```\nГотово.",
        "План: " + json.dumps({"steps": [STEP]}) + " — как просили.",
        '{"steps": [{"tool": "group_by", "arguments": {"by": ["region"],},},],}',
        '{"steps": [], "result_step": None, "dashboard_requested": False}',
        '{\n  // шаги\n  "steps": []\n}',
    ],
)
def test_loads_lenient_recovers_common_shapes(text: str) -> None:
    assert isinstance(loads_lenient(text), dict)


def test_braces_inside_strings_do_not_break_extraction() -> None:
    text = 'Ответ {не json} и затем {"purpose": "скобка } в строке", "steps": []}'
    assert loads_lenient(text)["purpose"] == "скобка } в строке"


def test_truncated_answer_is_closed() -> None:
    assert loads_lenient('{"steps": [{"tool": "aggregate"') == {"steps": [{"tool": "aggregate"}]}


def test_no_json_raises() -> None:
    with pytest.raises(ValueError):
        loads_lenient("Не могу ответить")


def test_bare_list_of_steps_becomes_plan() -> None:
    plan = parse_structured(ResearchPlan, json.dumps([STEP, {"tool": "sort_limit"}]))
    assert [s.tool for s in plan.steps] == ["group_by", "sort_limit"]


def test_wrapped_plan_is_unwrapped() -> None:
    plan = parse_structured(ResearchPlan, json.dumps({"ResearchPlan": {"steps": [STEP]}}))
    assert plan.steps[0].tool == "group_by"


def test_single_step_becomes_plan() -> None:
    assert parse_structured(ResearchPlan, json.dumps(STEP)).steps[0].tool == "group_by"


def test_tool_call_shaped_steps() -> None:
    raw = json.dumps([{"name": "group_by", "arguments": STEP["arguments"]}])
    plan = parse_structured(ResearchPlan, raw)
    assert plan.steps[0].tool == "group_by"
    assert plan.steps[0].arguments["column"] == "amount"


def test_insight_from_list_takes_first() -> None:
    insight = parse_structured(Insight, json.dumps([{"headline": "ok"}, {"headline": "x"}]))
    assert insight.headline == "ok"


def test_invalid_schema_raises_parse_error() -> None:
    with pytest.raises(StructuredParseError):
        parse_structured(Insight, json.dumps({"details": ["нет headline"]}))


def test_merge_multiple_plan_tool_calls() -> None:
    calls = [
        ("ResearchPlan", json.dumps({"steps": [STEP], "final_viz": "bar"})),
        ("ResearchPlan", json.dumps({"steps": [{"tool": "sort_limit"}]})),
    ]
    plan = merge_tool_calls(ResearchPlan, calls)
    assert [s.tool for s in plan.steps] == ["group_by", "sort_limit"]
    assert plan.final_viz == "bar"


def test_merge_tool_calls_named_after_tools() -> None:
    calls = [("filter_rows", json.dumps({"column": "amount", "operator": ">", "value": 150}))]
    plan = merge_tool_calls(ResearchPlan, calls)
    assert plan.steps[0].tool == "filter_rows"
    assert plan.steps[0].arguments["value"] == 150


# --- LLMClient в режиме llm_structured_mode=json_schema ---


def _text_response(content: str) -> dict:
    return {
        "id": "c1",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
    }


async def test_json_schema_mode_one_call_and_repair(monkeypatch) -> None:
    import httpx

    from app.config import settings
    from tests.unit.test_llm_client_retries import client_with

    monkeypatch.setattr(settings, "llm_structured_mode", "json_schema")
    llm, requests = client_with(
        [
            httpx.Response(200, json=_text_response('{"details": []}')),
            httpx.Response(200, json=_text_response('```json\n{"headline": "ok",}\n```')),
        ]
    )
    insight = await llm.complete_structured("s", "u", Insight)
    assert insight.headline == "ok"
    assert len(requests) == 2
    first = json.loads(requests[0].content)
    assert first["response_format"]["type"] == "json_schema"
    assert "tools" not in first
    # Переспрос несёт ошибку разбора, префикс промпта прежний.
    second_user = json.loads(requests[1].content)["messages"][1]["content"]
    assert second_user.startswith("u") and "headline" in second_user
