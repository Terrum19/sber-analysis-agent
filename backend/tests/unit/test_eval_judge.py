import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

spec = importlib.util.spec_from_file_location("eval_judge", REPO_ROOT / "scripts" / "eval_judge.py")
eval_judge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eval_judge)

POINTS = ["grounded", "period_full"]


def reply(**marks: bool) -> str:
    return json.dumps(
        {
            "items": {
                point: {"pass": passed, "why": f"{point} {passed}"}
                for point, passed in marks.items()
            }
        },
        ensure_ascii=False,
    )


class FakeJudge:
    def __init__(self, replies: list) -> None:
        self.replies = list(replies)
        self.prompts: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str) -> str:
        self.prompts.append((system, user))
        answer = self.replies.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


async def run_judge(replies: list, points=POINTS, **kwargs):
    client = FakeJudge(replies)
    verdict = await eval_judge.judge(
        "Какой период?", "С 2024 по 2026", [{"год": 2024}], points, client, **kwargs
    )
    return verdict, client


async def test_majority_per_point():
    verdict, client = await run_judge(
        [
            reply(grounded=True, period_full=False),
            reply(grounded=True, period_full=True),
            reply(grounded=False, period_full=False),
        ]
    )
    assert len(client.prompts) == 3
    assert verdict["grounded"][0] is True
    assert verdict["grounded"][1][0] == "2/3"
    assert verdict["period_full"][0] is False
    assert verdict["period_full"][1][0] == "1/3"
    assert "period_full False" in verdict["period_full"][1]


async def test_tie_fails():
    verdict, _client = await run_judge(
        [reply(grounded=True, period_full=True), reply(grounded=False, period_full=True)],
        votes=2,
    )
    assert verdict["grounded"][0] is False
    assert verdict["period_full"][0] is True


async def test_unparseable_reply_is_failed_vote():
    verdict, _client = await run_judge(
        [
            "я не знаю",
            reply(grounded=True, period_full=True),
            reply(grounded=False, period_full=True),
        ]
    )
    assert verdict["grounded"][0] is False
    assert any(eval_judge.UNPARSED in reason for reason in verdict["grounded"][1])
    assert verdict["period_full"] == (True, ["2/3", "period_full True"])


async def test_all_votes_unparseable():
    verdict, _client = await run_judge(["", "{broken", '{"items": []}'])
    for point in POINTS:
        passed, reasons = verdict[point]
        assert passed is False
        assert reasons[0] == "0/3"
        assert all(eval_judge.UNPARSED in reason for reason in reasons[1:])


async def test_client_error_is_failed_vote():
    verdict, _client = await run_judge(
        [
            RuntimeError("429"),
            reply(grounded=True, period_full=True),
            reply(grounded=True, period_full=True),
        ]
    )
    assert verdict["grounded"] == (True, ["2/3", "grounded True"])


async def test_missing_point_fails_that_point():
    verdict, _client = await run_judge([reply(grounded=True)], votes=1)
    assert verdict["grounded"][0] is True
    assert verdict["period_full"] == (False, ["0/1", eval_judge.NOT_RATED])


async def test_fenced_reply_is_parsed():
    fenced = "Вот оценка:\n```json\n" + reply(grounded=True, period_full=True) + "\n```"
    verdict, _client = await run_judge([fenced], votes=1)
    assert verdict["grounded"][0] is True
    assert verdict["period_full"][0] is True


async def test_prompt_has_points_and_notes():
    _verdict, client = await run_judge(
        [reply(grounded=True, period_full=True)], votes=1, notes="Данные с\n  10.2024 по 09.2026."
    )
    system, prompt = client.prompts[0]
    assert system == eval_judge.JUDGE_SYSTEM
    for point in POINTS:
        assert f"- {point}: {eval_judge.RUBRIC[point]}" in prompt
    assert "Эталонные факты (проверены по данным): Данные с 10.2024 по 09.2026." in prompt
    assert "Какой период?" in prompt
    assert "<<<\nС 2024 по 2026\n>>>" in prompt


def test_prompt_caps_rows():
    rows = [{"номер": index} for index in range(eval_judge.JUDGE_ROWS + 1)]
    prompt = eval_judge.build_prompt("Вопрос", "Ответ", rows, ["grounded"])
    assert f"первые {eval_judge.JUDGE_ROWS} строк из {eval_judge.JUDGE_ROWS + 1}" in prompt
    table = json.loads(prompt.split("на графике:\n", 1)[1].split("\n", 1)[0])
    assert table == rows[: eval_judge.JUDGE_ROWS]


def test_prompt_without_rows_or_answer():
    prompt = eval_judge.build_prompt("Вопрос", "", None, ["grounded"])
    assert "Таблицы результата нет" in prompt
    assert f"<<<\n{eval_judge.EMPTY_ANSWER}\n>>>" in prompt
    assert "Эталонные факты" not in prompt


def test_prompt_serializes_non_json_values():
    from datetime import date
    from decimal import Decimal

    prompt = eval_judge.build_prompt(
        "Вопрос", "Ответ", [{"day": date(2024, 1, 1), "sum": Decimal("1.5")}], ["grounded"]
    )
    assert '"2024-01-01"' in prompt and '"1.5"' in prompt


def test_unknown_point_rejected():
    with pytest.raises(ValueError, match="no_such_point"):
        eval_judge.build_prompt("Вопрос", "Ответ", None, ["grounded", "no_such_point"])


def test_rubric_points():
    assert eval_judge.rubric_points(1, True, ["period_full"]) == [
        "answers_question",
        "grounded",
        "business_readable",
        "period_full",
    ]
    assert eval_judge.rubric_points(1, False, ["period_full"]) == ["period_full"]
    assert eval_judge.rubric_points(0, False, ["refuse_or_redirect"]) == [
        "business_readable",
        "refuse_or_redirect",
    ]


def test_client_from_env_requires_base_url():
    with pytest.raises(RuntimeError, match="JUDGE_BASE_URL"):
        eval_judge.judge_client_from_env({})
    client = eval_judge.judge_client_from_env({"JUDGE_BASE_URL": "http://judge.invalid/v1"})
    assert client.model == eval_judge.DEFAULT_JUDGE_MODEL
