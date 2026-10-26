import asyncio
import json
import logging
import os
import re
from collections.abc import Sequence
from typing import Protocol

logger = logging.getLogger("eval.judge")

GENERAL_POINTS = ("answers_question", "grounded", "business_readable")
JUDGE_ROWS = 30
DEFAULT_VOTES = 3
DEFAULT_JUDGE_MODEL = "deepseek-flash"
JUDGE_TIMEOUT_S = 180
EMPTY_ANSWER = "(пусто)"
UNPARSED = "ответ судьи не разобран"
NOT_RATED = "судья не оценил пункт"

RUBRIC = {
    "answers_question": "Ответ отвечает именно на заданный вопрос, а не на соседний.",
    "grounded": (
        "Все числа и факты в ответе подтверждаются таблицей результата или эталонными "
        "фактами; ничего не выдумано."
    ),
    "business_readable": (
        "Ответ понятен бизнес-пользователю без технических пояснений: нет SQL, "
        "внутренних имён таблиц вроде temp_result, английских служебных слов."
    ),
    "describes_domain": (
        "Объясняет простыми словами, что это за данные (о чём таблица, что в строке)."
    ),
    "period_full": "Называет период данных целиком: и начало, и конец.",
    "field_meaning": "Правильно объясняет смысл поля (сверь с эталонными фактами и данными).",
    "july_not_special": (
        "Не придумывает особый июльский эффект: июль по выручке обычный месяц, "
        "пик — декабрь. Ответ «ничего особенного/на уровне других месяцев» верный."
    ),
    "numbers_grounded": "Выводы подкреплены конкретными числами из данных.",
    "duplicates_none": "Верно говорит, что полных дублей нет (или что повторов не найдено).",
    "rate_vs_count": (
        "Сравнивает не только абсолютное число ошибок, но и долю ошибок от запусков "
        "(или оговаривает, что лидер просто чаще запускается)."
    ),
    "no_real_difference": (
        "Замечает, что разница между перевозчиками мизерная (все около 8,5 %), "
        "а не объявляет явного «худшего» без оговорки."
    ),
    "chart_fits": "Тип графика подходит под вопрос и форму данных.",
    "follows_context": (
        "Понимает, о чём «второй пункт» из прошлого ответа в диалоге, и раскрывает "
        "именно его, а не начинает с нуля."
    ),
    "winter_peak": (
        "Верно подтверждает зимний пик продаж и показывает его числами. Называть "
        "конкретные препараты (ксилометазолин, парацетамол) не обязательно, но если "
        "названы — должны быть верными."
    ),
    "spring_peak": (
        "Верно называет весенний пик (апрель–май) у антигистаминных; номер месяца "
        "вместо названия тоже считается, если он верный."
    ),
    "vk_unprofitable": (
        "Замечает, что VK Реклама убыточна или заметно хуже окупается (выручка меньше расходов)."
    ),
    "causation_caveat": "Не выдаёт корреляцию за причинность; оговаривает ограничения вывода.",
    "forecast_caveat": (
        "Даёт оценку с допущениями и оговорками (сезонность, неопределённость), а не "
        "голую цифру как факт."
    ),
    "refuse_or_redirect": (
        "Вопрос некорректный или вне данных. Хорошо: честно сказать, что так нельзя/"
        "таких данных нет, и предложить, что можно спросить по этим данным. Плохо: "
        "выдумать ответ, выполнить разрушительное действие, ответить не по делу."
    ),
}

JUDGE_SYSTEM = (
    "Ты строгий проверяющий ответов BI-агента для бизнес-пользователей. Оцени ответ по "
    "каждому пункту рубрики независимо: pass=true только если пункт выполнен явно. "
    "Отвечай только JSON-объектом без markdown."
)

FENCED = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class JudgeLLM(Protocol):
    async def complete(self, system: str, user: str) -> str: ...


class JudgeClient:
    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        from openai import AsyncOpenAI

        self.model = model
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=JUDGE_TIMEOUT_S)

    async def complete(self, system: str, user: str) -> str:
        response = await self._client.chat.completions.create(
            model=self.model,
            temperature=0,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or ""


def judge_client_from_env(environ: dict[str, str] | None = None) -> JudgeClient:
    environ = os.environ if environ is None else environ
    base_url = environ.get("JUDGE_BASE_URL")
    if not base_url:
        raise RuntimeError("для судьи нужен JUDGE_BASE_URL (и JUDGE_API_KEY, JUDGE_MODEL)")
    return JudgeClient(
        base_url=base_url,
        api_key=environ.get("JUDGE_API_KEY") or "not-needed",
        model=environ.get("JUDGE_MODEL") or DEFAULT_JUDGE_MODEL,
    )


def rubric_points(scenario: int, done: bool, task_points: Sequence[str] = ()) -> list[str]:
    points = []
    if scenario != 0 and done:
        points += GENERAL_POINTS
    elif scenario == 0:
        points.append("business_readable")
    points += [point for point in task_points if point not in points]
    return points


def check_points(points: Sequence[str]) -> None:
    unknown = [point for point in points if point not in RUBRIC]
    if unknown:
        raise ValueError(f"неизвестные пункты рубрики судьи: {', '.join(unknown)}")


def rows_block(rows: list[dict] | None, max_rows: int) -> str:
    if not rows:
        return (
            "Таблицы результата нет: агент отвечал по исходной таблице или ответ без "
            "расчёта. Числа сверяй со справкой и эталонами; не считай число выдуманным "
            "только потому, что таблицы нет."
        )
    shown = rows[:max_rows]
    return (
        f"Таблица результата агента (первые {len(shown)} строк из {len(rows)}), "
        "пользователь видит её на графике:\n" + json.dumps(shown, ensure_ascii=False, default=str)
    )


def build_prompt(
    question: str,
    answer: str,
    rows: list[dict] | None,
    points: Sequence[str],
    notes: str | None = None,
    facts: str | None = None,
    previous: tuple[str, str] | None = None,
    max_rows: int = JUDGE_ROWS,
) -> str:
    check_points(points)
    lines = [f"Вопрос пользователя: {question}"]
    if facts:
        lines.append(f"Справка по исходной таблице (посчитана по данным, верна): {facts}")
    if previous is not None:
        previous_question, previous_answer = previous
        lines.append(f"Предыдущий вопрос в диалоге: {previous_question}")
        lines.append(f"Предыдущий ответ агента:\n<<<\n{previous_answer or EMPTY_ANSWER}\n>>>")
    if notes:
        lines.append(f"Эталонные факты (проверены по данным): {' '.join(notes.split())}")
    lines += [
        "Ответ агента — ровно то, что видит пользователь, между <<< и >>>:",
        f"<<<\n{answer or EMPTY_ANSWER}\n>>>",
        rows_block(rows, max_rows),
        "",
        "Рубрика:",
        *(f"- {point}: {RUBRIC[point]}" for point in points),
        "",
        (
            'Ответь JSON: {"items": {"<ключ>": {"pass": true|false, "why": "<одно предложение>"}}} '
            "со всеми ключами рубрики."
        ),
    ]
    return "\n".join(lines)


def parse_reply(content: str) -> dict:
    content = content.strip()
    fenced = FENCED.search(content)
    if fenced:
        content = fenced.group(1)
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < start:
        raise ValueError("в ответе нет JSON-объекта")
    return json.loads(content[start : end + 1])


def read_vote(content: str, points: Sequence[str]) -> dict[str, tuple[bool, str]]:
    try:
        items = parse_reply(content)["items"]
        if not isinstance(items, dict):
            raise TypeError("items — не объект")
    except (ValueError, KeyError, TypeError) as error:
        return {point: (None, f"{UNPARSED}: {error}") for point in points}
    vote = {}
    for point in points:
        item = items.get(point)
        if not isinstance(item, dict) or "pass" not in item:
            vote[point] = (None, NOT_RATED)
            continue
        vote[point] = (item["pass"] is True, str(item.get("why", "")))
    return vote


async def cast_vote(client: JudgeLLM, prompt: str, points: Sequence[str]) -> dict:
    try:
        content = await client.complete(JUDGE_SYSTEM, prompt)
    except Exception as error:
        logger.warning("судья не ответил: %s", error, exc_info=True)
        return {
            point: (None, f"судья не ответил: {type(error).__name__}: {error}") for point in points
        }
    return read_vote(content, points)


def majority(
    ballots: list[dict], points: Sequence[str]
) -> dict[str, tuple[bool | None, list[str]]]:
    verdict = {}
    for point in points:
        cast = [ballot[point] for ballot in ballots if ballot[point][0] is not None]
        skipped = [ballot[point][1] for ballot in ballots if ballot[point][0] is None]
        if not cast:
            verdict[point] = (None, ["0/0", *dict.fromkeys(skipped)])
            continue
        ayes = sum(agreed for agreed, reason in cast)
        passed = ayes * 2 > len(cast)
        reasons = [reason for agreed, reason in cast if agreed == passed]
        verdict[point] = (passed, [f"{ayes}/{len(cast)}", *dict.fromkeys(reasons)])
    return verdict


async def judge(
    question: str,
    answer: str,
    rows: list[dict] | None,
    points: Sequence[str],
    client: JudgeLLM,
    votes: int = DEFAULT_VOTES,
    notes: str | None = None,
    facts: str | None = None,
    previous: tuple[str, str] | None = None,
) -> dict[str, tuple[bool | None, list[str]]]:
    if votes < 1:
        raise ValueError("судье нужен хотя бы один голос")
    prompt = build_prompt(question, answer, rows, points, notes, facts, previous)
    ballots = await asyncio.gather(
        *(cast_vote(client, prompt, points) for vote_index in range(votes))
    )
    return majority(list(ballots), points)
