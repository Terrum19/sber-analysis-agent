"""Токены хода по местам: оценка по кускам, пока вызов идёт, затем точные числа."""

from app.core.llm import usage as usage_module
from app.core.llm.usage import llm_scope, record_usage, start_call, track_usage


def test_scopes_nest_and_keys_match_progress():
    sent: list[dict] = []
    track_usage(sink=sent.append)

    with llm_scope(tile_id=2):
        with llm_scope(stage="planning"):
            record_usage({"prompt_tokens": 10, "completion_tokens": 1})
        with llm_scope(step_id=5):
            record_usage({"prompt_tokens": 20, "completion_tokens": 2})
            with llm_scope(stage="insight"):
                record_usage({"prompt_tokens": 5, "completion_tokens": 1})
    record_usage({"prompt_tokens": 1, "completion_tokens": 1})

    assert [event["data"]["key"] for event in sent] == [
        "tile:2/planning",
        "tile:2/step:5",
        "tile:2/insight",
        "turn",
    ]
    assert sent[1]["data"]["step_id"] == 5 and sent[1]["data"]["tile_id"] == 2


def test_live_estimate_is_replaced_by_exact_usage(monkeypatch):
    clock = iter(range(100))
    monkeypatch.setattr(usage_module.time, "monotonic", lambda: next(clock))
    sent: list[dict] = []
    turn = track_usage(sink=sent.append)

    with llm_scope(stage="outline"):
        call = start_call()
    for _ in range(3):
        call.tick()
    assert sent[-1]["data"]["completion_tokens"] == 3 and sent[-1]["data"]["live"]

    call.finish({"prompt_tokens": 100, "completion_tokens": 40})
    assert sent[-1]["data"] | {} == {
        "stage": "outline",
        "key": "outline",
        "prompt_tokens": 100,
        "completion_tokens": 40,
        "live": False,
    }
    assert turn.as_event()["total_tokens"] == 140


def test_ticks_are_throttled(monkeypatch):
    monkeypatch.setattr(usage_module.time, "monotonic", lambda: 1000.0)
    sent: list[dict] = []
    track_usage(sink=sent.append)
    call = start_call()
    for _ in range(50):
        call.tick()
    # Первый кусок отправлен, остальные в пределах TICK_INTERVAL_S — нет.
    assert len(sent) == 1


def test_no_usage_from_server_counts_chunks():
    turn = track_usage()
    call = start_call()
    call.tick()
    call.tick()
    call.finish(None)
    assert turn.as_event() == {
        "llm_calls": 1,
        "prompt_tokens": 0,
        "completion_tokens": 2,
        "total_tokens": 2,
    }


def test_outside_turn_nothing_is_counted():
    usage_module._usage.set(None)
    assert start_call() is None
    record_usage({"prompt_tokens": 1})


def test_phase_prefixes_review_and_repair_keys():
    """«Проверка»: критик — review, исправление — repair/… с теми же ключами, что у хода."""
    sent: list[dict] = []
    track_usage(sink=sent.append)

    with llm_scope(phase="review"):
        record_usage({"prompt_tokens": 1, "completion_tokens": 1})
    with llm_scope(phase="repair"):
        with llm_scope(stage="planning"):
            record_usage({"prompt_tokens": 1, "completion_tokens": 1})
        with llm_scope(step_id=12):
            record_usage({"prompt_tokens": 1, "completion_tokens": 1})

    assert [event["data"]["key"] for event in sent] == [
        "review",
        "repair/planning",
        "repair/step:12",
    ]
