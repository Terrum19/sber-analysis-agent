#!/usr/bin/env python3
"""Оценка прогона eval: проверки по эталонам, LLM-судья, отчёт.

Читает results.jsonl из каталога прогона (scripts/eval/run_eval.py), пишет туда
scores.jsonl и report.md. Судья — отдельный OpenAI-совместимый эндпоинт
(JUDGE_BASE_URL, JUDGE_API_KEY, JUDGE_MODEL); без --judge считаются только
детерминированные проверки.

    python3 scripts/eval/score.py <run_dir> [--judge] [--out-report docs/reports/...]
"""

import argparse
import asyncio
import json
import logging
import os
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger("eval.score")
EVAL_DIR = ROOT / "data" / "eval" / "hk"
# 0,5 %: «1,16 млрд» и «~43 %» проходят, сумма с другим фильтром (0,8 %) — нет.
DEFAULT_TOLERANCE = 0.005
SCALE = {"тыс": 1e3, "млн": 1e6, "млрд": 1e9, "трлн": 1e12}
# 2 706 019 438 | 1,16 | 43.5 | 1 159 744 622,42; за ним — необязательный множитель.
NUMBER = re.compile(
    r"(?<![\w.,])(-?\d{1,3}(?:[   ]\d{3})+|-?\d+)(?:[.,](\d+))?"
    r"(?:\s*(тыс|млн|млрд|трлн)\.?)?",
    re.IGNORECASE,
)

# Рубрика судьи: общие пункты для обычного ответа и пункты под задачу (judge: [...]).
GENERAL_RUBRIC = {
    "answers_question": "Ответ отвечает именно на заданный вопрос, а не на соседний.",
    "grounded": (
        "Все числа и факты в ответе подтверждаются таблицей результата или эталонными "
        "фактами; ничего не выдумано."
    ),
    "business_readable": (
        "Ответ понятен бизнес-пользователю без технических пояснений: нет SQL, "
        "внутренних имён таблиц вроде temp_result, английских служебных слов."
    ),
}
TASK_RUBRIC = {
    "describes_domain": "Объясняет простыми словами, что это за данные (о чём таблица, что в строке).",
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
    "causation_caveat": ("Не выдаёт корреляцию за причинность; оговаривает ограничения вывода."),
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


# --- Разбор чисел и текста ---


def numbers_in_text(text: str) -> list[float]:
    found = []
    for whole, fraction, scale in NUMBER.findall(text):
        value = float(re.sub(r"[   ]", "", whole) + (f".{fraction}" if fraction else ""))
        if scale:
            value *= SCALE[scale.lower()]
        found.append(value)
    return found


def numbers_in_rows(rows: list[dict] | None) -> list[float]:
    values = []
    for row in rows or []:
        for value in row.values():
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                values.append(float(value))
    return values


# Сбой стадии из события progress («insight: …») — служебный, пользователь его не видит.
STAGE_ERROR = re.compile(r"^[a-z_]+: ")


def answer_text(record: dict) -> str:
    """Ровно то, что видит пользователь: текст, уточнение, ошибка, подпись графика."""
    parts = []
    for text in record["text"]:
        parts.append(text["headline"])
        parts.extend(text["details"])
    if record["clarification"]:
        parts.append(record["clarification"]["error"])
        parts.extend(record["clarification"]["suggestions"])
    parts.extend(error for error in record["errors"] if not STAGE_ERROR.match(error))
    # Подпись графика — это headline инсайта; в UI она стоит у графика, а не вторым абзацем.
    parts.extend(chart["caption"] for chart in record["charts"] if chart["caption"] not in parts)
    return "\n".join(part for part in parts if part)


def close(candidate: float, gold: float, tolerance: float, tolerance_abs: float | None) -> bool:
    if tolerance_abs is not None:
        return abs(candidate - gold) <= tolerance_abs
    if gold == 0:
        return abs(candidate) < 1e-9
    return abs(candidate - gold) <= tolerance * abs(gold)


def matched(gold: float, candidates: list[float], tolerance: float, tolerance_abs) -> bool:
    options = [gold]
    if 0 < abs(gold) <= 1:
        options.append(gold * 100)  # доля как процент
    elif abs(gold) <= 100:
        options.append(gold / 100)  # процент как доля
    return any(
        close(candidate, option, tolerance, tolerance_abs)
        for option in options
        for candidate in candidates
    )


# --- Проверки ---


def lower(text: str) -> str:
    return text.lower().replace("ё", "е")


def check(task: dict, gold: dict, record: dict, parent: dict | None) -> dict:
    """Детерминированные проверки задачи: {имя: (pass, пояснение)}."""
    expect = task.get("expect") or {}
    text = answer_text(record)
    low = lower(text)
    candidates = numbers_in_text(text) + numbers_in_rows(record["result"])
    results = {}

    if task["scenario"] != 0:
        results["done"] = (record["status"] == "done", record["status"])

    if "numbers" in gold:
        spec = expect["numbers"]
        tolerance = spec.get("tolerance", DEFAULT_TOLERANCE)
        tolerance_abs = spec.get("tolerance_abs")
        best = (0, 1)
        for variant in gold["numbers"]:
            values = [value for row in variant for value in row if isinstance(value, (int, float))]
            hits = sum(matched(value, candidates, tolerance, tolerance_abs) for value in values)
            if values and hits / len(values) >= best[0] / best[1]:
                best = (hits, len(values))
        results["numbers"] = (best[0] == best[1], f"{best[0]}/{best[1]} эталонных чисел")

    if "range" in gold:
        low_value, high_value = gold["range"]
        inside = [value for value in numbers_in_text(text) if low_value <= value <= high_value]
        results["range"] = (
            bool(inside),
            f"в тексте {'есть' if inside else 'нет'} числа в [{low_value:,.0f}; {high_value:,.0f}]",
        )

    groups = []
    for key in ("contains_all", "contains_any_groups"):
        groups += expect.get(key, [])
    if "contains_any" in expect:
        groups.append(expect["contains_any"])
    if "contains_any" in gold:
        groups.append(gold["contains_any"])
    if groups:
        missing = [group for group in groups if not any(lower(word) in low for word in group)]
        results["contains"] = (not missing, f"нет: {missing}" if missing else "ок")

    if "chart_any" in expect:
        chart = record["charts"][-1]["type"] if record["charts"] else None
        results["chart"] = (chart in expect["chart_any"], f"график {chart}")
    if expect.get("chart_changed"):
        before = parent["charts"][-1]["type"] if parent and parent["charts"] else None
        after = record["charts"][-1]["type"] if record["charts"] else None
        results["chart_changed"] = (
            after is not None and after != before,
            f"{before} -> {after}",
        )
    if expect.get("dashboard"):
        results["dashboard"] = (record["dashboard"] is not None, record["dashboard"] or "нет")
    if expect.get("table_intact"):
        results["table_intact"] = (
            record["dataset_rows"] == gold["row_count"],
            f"строк {record['dataset_rows']} из {gold['row_count']}",
        )
    return results


def column_recall(task: dict, record: dict) -> float | None:
    wanted = (task.get("expect") or {}).get("columns_all")
    if not wanted:
        return None
    return sum(column in record["selected_columns"] for column in wanted) / len(wanted)


# --- Судья ---


def rubric_for(task: dict, record: dict) -> dict[str, str]:
    items = {}
    if task["scenario"] != 0 and record["status"] == "done":
        items.update(GENERAL_RUBRIC)
    elif task["scenario"] == 0:
        items["business_readable"] = GENERAL_RUBRIC["business_readable"]
    for key in task.get("judge", []):
        items[key] = TASK_RUBRIC[key]
    return items


def dataset_facts(db_path: Path, table: str, cache: dict) -> str:
    """Справка по исходной таблице: по ней судья сверяет ответы без таблицы результата."""
    if table in cache:
        return cache[table]
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        [(rows,)] = con.execute(f'SELECT count(*) FROM "{table}"').fetchall()
        columns = con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = ? ORDER BY ordinal_position",
            [table],
        ).fetchall()
        periods = []
        for name, kind in columns:
            if kind in ("DATE", "TIMESTAMP"):
                [(low, high)] = con.execute(
                    f'SELECT min("{name}")::DATE, max("{name}")::DATE FROM "{table}"'
                ).fetchall()
                periods.append(f"«{name}»: {low} — {high}")
    finally:
        con.close()
    cache[table] = (
        f"строк {rows}; колонки: "
        + ", ".join(f"«{name}» ({kind})" for name, kind in columns)
        + "; периоды: "
        + ("; ".join(periods) or "нет дат")
    )
    return cache[table]


def judge_prompt(
    task: dict, gold: dict, record: dict, parent: dict | None, rubric: dict, facts: str
) -> str:
    result = record["result"] or []
    lines = [
        f"Вопрос пользователя: {task['question']}",
        f"Справка по исходной таблице (посчитана по данным, верна): {facts}",
    ]
    if parent is not None:
        lines.append(f"Предыдущий вопрос в диалоге: {parent['question']}")
        lines.append(f"Предыдущий ответ агента:\n<<<\n{answer_text(parent) or '(пусто)'}\n>>>")
    if task.get("notes"):
        lines.append(f"Эталонные факты (проверены по данным): {task['notes']}")
    if gold:
        lines.append(f"Эталонные значения: {json.dumps(gold, ensure_ascii=False)}")
    if result:
        table = (
            f"Таблица результата агента (первые {min(len(result), 30)} строк из "
            f"{len(result)}), пользователь видит её на графике:\n"
            + json.dumps(result[:30], ensure_ascii=False, default=str)
        )
    else:
        table = (
            "Таблицы результата нет: агент отвечал по исходной таблице или ответ без "
            "расчёта. Числа сверяй со справкой и эталонами; не считай число выдуманным "
            "только потому, что таблицы нет."
        )
    lines += [
        f"Статус хода агента: {record['status']}",
        "Ответ агента — ровно то, что видит пользователь, между <<< и >>>:",
        f"<<<\n{answer_text(record) or '(пусто)'}\n>>>",
        (
            "Служебные сведения (пользователь их не видит, в оценку текста не включай): "
            f"тип графика {record['charts'][-1]['type'] if record['charts'] else 'нет'}."
        ),
        table,
        "",
        "Рубрика:",
        *(f"- {key}: {text}" for key, text in rubric.items()),
        "",
        (
            'Ответь JSON: {"items": {"<ключ>": {"pass": true|false, "why": "<одно предложение>"}}} '
            "со всеми ключами рубрики."
        ),
    ]
    return "\n".join(lines)


def parse_json(content: str) -> dict:
    content = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
    if fenced:
        content = fenced.group(1)
    start, end = content.find("{"), content.rfind("}")
    return json.loads(content[start : end + 1])


async def judge(client, model: str, prompt: str, rubric: dict) -> dict | None:
    """Одна оценка судьи; None — судья так и не ответил (это не провал ответа)."""
    import openai

    for attempt in range(4):
        try:
            response = await client.chat.completions.create(
                model=model,
                temperature=0,
                messages=[
                    {"role": "system", "content": JUDGE_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
            items = parse_json(response.choices[0].message.content or "")["items"]
            return {
                key: {"pass": bool(items[key]["pass"]), "why": str(items[key].get("why", ""))}
                for key in rubric
            }
        except openai.APIStatusError as error:
            # 402 у usekey — резерв оплаты под одновременные запросы, 429 — лимит.
            if error.status_code not in (402, 429, 500, 502, 503, 524):
                logger.warning("судья: %s", error)
                return None
            await asyncio.sleep(20 * (attempt + 1))
        except Exception as error:  # noqa: BLE001 — битый JSON или обрыв: повтор
            logger.warning("судья: %s", error)
    return None


async def judge_votes(client, model: str, prompt: str, rubric: dict, votes: int) -> dict:
    """Несколько независимых оценок, по каждому пункту — большинство.

    Один прогон судьи меняет вердикт пункта примерно в 14 % случаев (замер на
    eval 25.10), поэтому одиночной оценке при сравнении версий верить нельзя.
    Не ответившие голоса отбрасываются; если не ответил никто — пункты без оценки.
    """
    runs = await asyncio.gather(*(judge(client, model, prompt, rubric) for _ in range(votes)))
    runs = [run for run in runs if run is not None]
    if not runs:
        return {key: {"pass": None, "why": "судья не ответил", "votes": "0/0"} for key in rubric}
    verdict = {}
    for key in rubric:
        passes = [run[key]["pass"] for run in runs]
        majority = sum(passes) * 2 > len(passes)
        why = next(run[key]["why"] for run in runs if run[key]["pass"] == majority)
        verdict[key] = {"pass": majority, "why": why, "votes": f"{sum(passes)}/{len(passes)}"}
    return verdict


# --- Отчёт ---

MARK = {True: "✅", False: "❌", None: "❔"}


def verdict_pass(verdict: dict) -> bool | None:
    """Все пункты судьи пройдены; None — хоть один пункт без оценки."""
    if any(item["pass"] is None for item in verdict.values()):
        return None
    return all(item["pass"] for item in verdict.values())


def percent(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f} % ({part}/{whole})" if whole else "—"


def quantile(values: list[float], q: float) -> str:
    if not values:
        return "—"
    if len(values) == 1:
        return f"{values[0]:.0f}"
    return f"{statistics.quantiles(values, n=100, method='inclusive')[int(q * 100) - 1]:.0f}"


def render(meta: dict, tasks: dict, scores: list[dict]) -> str:
    total = len(scores)
    passed = sum(score["pass"] is True for score in scores)
    unjudged = sum(score["judge_pass"] is None for score in scores)
    det = sum(score["checks_pass"] for score in scores)
    judged = [score for score in scores if score["judge"] and score["judge_pass"] is not None]
    judge_ok = sum(score["judge_pass"] for score in judged)
    seconds = [score["seconds"] for score in scores]
    recalls = [score["column_recall"] for score in scores if score["column_recall"] is not None]
    lines = [
        f"# Eval агента на датасетах организаторов — {meta['started'][:10]}",
        "",
        (
            f"Модель агента: `{meta['model']}` ({meta['base_url']}), structured mode "
            f"`{meta['structured_mode']}`, песочница {'есть' if meta['sandbox'] else 'нет'}. "
            f"Судья: `{meta.get('judge_model') or 'не запускался'}`. Повторов: {meta['repeat']}."
        ),
        "",
        (
            f"Код агента: `{meta.get('commit', '?')}`; продолжения диалога "
            f"{'с reply_to' if meta.get('reply_to') else 'без reply_to'}."
        ),
        "",
        "| Метрика | Значение |",
        "|---|---|",
        f"| Задача решена (проверки + судья) | {percent(passed, total)} |",
        f"| Детерминированные проверки | {percent(det, total)} |",
        f"| Судья: все пункты рубрики | {percent(judge_ok, len(judged))} |",
        f"| Без оценки судьи (судья не ответил) | {unjudged} |",
        f"| Исключено: ход сорван лимитом API провайдера | {meta.get('api_errors', 0)} |",
        (
            f"| Recall отбора колонок | "
            f"{(f'{100 * statistics.mean(recalls):.0f} %' if recalls else '—')} "
            f"({sum(r == 1 for r in recalls)}/{len(recalls)} полных) |"
        ),
        f"| Время хода p50 / p95, с | {quantile(seconds, 0.5)} / {quantile(seconds, 0.95)} |",
        (
            f"| Вызовов LLM на ход, среднее | "
            f"{statistics.mean(score['llm_calls'] for score in scores):.1f} |"
        ),
        (
            f"| Ходов без done (сбой, таймаут) | "
            f"{sum(score['status'] in ('no_done', 'timeout', 'exception') for score in scores)} |"
        ),
        "",
    ]
    if meta["repeat"] > 1:
        by_task = defaultdict(list)
        for score in scores:
            by_task[score["id"]].append(score["pass"] is True)
        all_k = sum(all(runs) for runs in by_task.values())
        any_k = sum(any(runs) for runs in by_task.values())
        lines += [
            (
                f"pass^{meta['repeat']} (решена во всех повторах): {percent(all_k, len(by_task))}; "
                f"pass@{meta['repeat']} (хотя бы раз): {percent(any_k, len(by_task))}."
            ),
            "",
        ]
    names = {
        0: "неудобные",
        1: "знакомство",
        2: "содержание",
        3: "группировки/аномалии",
        4: "визуализация",
        5: "дашборд",
        6: "диалог",
        7: "паттерны во времени",
        8: "сегменты",
        9: "корреляции/лаги",
        10: "прогноз",
    }
    lines += [
        "## По сценариям",
        "",
        "| Сценарий | Решено | Проверки | Судья |",
        "|---|---|---|---|",
    ]
    for scenario in sorted({score["scenario"] for score in scores}):
        group = [score for score in scores if score["scenario"] == scenario]
        judged_group = [
            score for score in group if score["judge"] and score["judge_pass"] is not None
        ]
        lines.append(
            f"| {scenario}. {names.get(scenario, '')} | "
            f"{percent(sum(s['pass'] is True for s in group), len(group))} | "
            f"{percent(sum(s['checks_pass'] for s in group), len(group))} | "
            f"{percent(sum(s['judge_pass'] for s in judged_group), len(judged_group))} |"
        )
    lines += ["", "## По датасетам", "", "| Таблица | Решено |", "|---|---|"]
    for dataset in sorted({score["dataset"] for score in scores}):
        group = [score for score in scores if score["dataset"] == dataset]
        lines.append(
            f"| `{dataset}` | {percent(sum(s['pass'] is True for s in group), len(group))} |"
        )
    lines += [
        "",
        "## Задачи",
        "",
        "| Задача | r | Итог | Статус | Время, с | Что не так |",
        "|---|---|---|---|---|---|",
    ]
    for score in sorted(scores, key=lambda s: (s["scenario"], s["id"], s["repeat"])):
        problems = [f"{name}: {why}" for name, (ok, why) in score["checks"].items() if not ok]
        problems += [
            f"судья {key}: {item['why']}"
            for key, item in score["judge"].items()
            if not item["pass"]
        ]
        if score["errors"]:
            problems.append(f"ошибки: {score['errors'][0][:160]}")
        cell = "; ".join(problems).replace("|", "/").replace("\n", " ")[:600]
        lines.append(
            f"| `{score['id']}` | {score['repeat']} | {MARK[score['pass']]} | "
            f"{score['status']} | {score['seconds']:.0f} | {cell} |"
        )
    lines += ["", "## Ответы", ""]
    for score in sorted(scores, key=lambda s: (s["scenario"], s["id"], s["repeat"])):
        task = tasks[score["id"]]
        lines += [
            f"### `{score['id']}` (r{score['repeat']}) — {task['question']}",
            "",
            f"Тулы: {', '.join(score['tools']) or '—'}; график: {score['chart'] or '—'}",
            "",
            *(f"> {line}" for line in (score["answer"] or "(нет ответа)").splitlines()),
            "",
        ]
    return "\n".join(lines) + "\n"


# --- main ---


def by_session(records: list[dict]) -> dict:
    """Предыдущий ход той же сессии — для chart_changed и рубрики диалога."""
    parents, last = {}, {}
    for record in records:
        key = (record["_dir"], record["session_id"], record["repeat"])
        parents[(record["id"], record["repeat"])] = last.get(key)
        last[key] = record
    return parents


API_LIMIT = re.compile(r"Error code: 429|rate_limit|concurrency_limit")


def api_failed(record: dict) -> bool:
    """Ход сорван лимитом провайдера (status api_error с run_eval; у старых — по ошибкам)."""
    if record["status"] == "api_error":
        return True
    texts = record["errors"] + [call["error"] or "" for call in record["llm_calls"]]
    return any(API_LIMIT.search(text) for text in texts)


def load_runs(run_dirs: list[Path]) -> tuple[dict, list[dict]]:
    """Прогоны одной версии вместе: повторы нумеруются подряд (pass^k по всем)."""
    meta, records, offset = None, [], 0
    for run_dir in run_dirs:
        run_meta = json.loads((run_dir / "meta.json").read_text())
        if meta is None:
            meta = dict(run_meta, runs=[])
        elif run_meta.get("commit") != meta.get("commit"):
            print(f"[!] {run_dir}: другой коммит {run_meta.get('commit')}", file=sys.stderr)
        meta["runs"].append(str(run_dir))
        for line in (run_dir / "results.jsonl").open():
            record = json.loads(line)
            record["_dir"] = str(run_dir)
            record["repeat"] += offset
            records.append(record)
        offset += run_meta["repeat"]
    meta["repeat"] = offset
    return meta, records


async def score_run(args: argparse.Namespace) -> int:
    tasks = {task["id"]: task for task in yaml.safe_load(args.questions.read_text("utf-8"))}
    gold_all = json.loads(args.gold.read_text("utf-8"))
    meta, records = load_runs(args.run_dirs)
    # Ход, сорванный лимитом провайдера, — не ответ агента: в оценку не идёт.
    kept = [record for record in records if not api_failed(record)]
    meta["api_errors"] = len(records) - len(kept)
    records = kept
    out_dir = args.out or args.run_dirs[0]
    out_dir.mkdir(parents=True, exist_ok=True)
    parents = by_session(records)

    client, model = None, None
    if args.judge:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            base_url=os.environ["JUDGE_BASE_URL"], api_key=os.environ["JUDGE_API_KEY"], timeout=180
        )
        model = os.environ.get("JUDGE_MODEL", "deepseek-flash")
        meta["judge_model"] = f"{model}, голосов {args.judge_votes}"
    semaphore = asyncio.Semaphore(args.judge_parallel)
    facts_cache: dict[str, str] = {}

    async def score_one(record: dict) -> dict:
        task, gold = tasks[record["id"]], gold_all.get(record["id"], {})
        parent = parents[(record["id"], record["repeat"])]
        checks = check(task, gold, record, parent)
        verdict = {}
        rubric = rubric_for(task, record)
        if client is not None and rubric:
            facts = dataset_facts(
                Path(record["_dir"]) / "main.duckdb", task["dataset"], facts_cache
            )
            prompt = judge_prompt(task, gold, record, parent, rubric, facts)
            async with semaphore:
                verdict = await judge_votes(client, model, prompt, rubric, args.judge_votes)
        checks_pass = all(ok for ok, _ in checks.values())
        judge_pass = verdict_pass(verdict)
        return {
            "id": record["id"],
            "repeat": record["repeat"],
            "scenario": record["scenario"],
            "dataset": record["dataset"],
            "status": record["status"],
            "seconds": record["seconds"],
            "llm_calls": len(record["llm_calls"]),
            "tools": record["tools"],
            "chart": record["charts"][-1]["type"] if record["charts"] else None,
            "answer": answer_text(record),
            "errors": record["errors"],
            "checks": checks,
            "checks_pass": checks_pass,
            "judge": verdict,
            "judge_pass": judge_pass,
            "pass": checks_pass and judge_pass if judge_pass is not None else None,
            "column_recall": column_recall(task, record),
        }

    scores = await asyncio.gather(*(score_one(record) for record in records))
    with (out_dir / "scores.jsonl").open("w", encoding="utf-8") as out:
        for score in scores:
            out.write(json.dumps(score, ensure_ascii=False) + "\n")
    report = render(meta, tasks, scores)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    if args.out_report:
        args.out_report.parent.mkdir(parents=True, exist_ok=True)
        args.out_report.write_text(report, encoding="utf-8")
    print(report.split("## По датасетам")[0])
    print(f"Отчёт: {out_dir / 'report.md'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Оценка прогона eval")
    parser.add_argument("run_dirs", type=Path, nargs="+", help="каталоги прогонов одной версии")
    parser.add_argument("--out", type=Path, help="куда писать отчёт (по умолчанию первый каталог)")
    parser.add_argument("--questions", type=Path, default=EVAL_DIR / "questions.yaml")
    parser.add_argument("--gold", type=Path, default=EVAL_DIR / "gold.json")
    parser.add_argument("--judge", action="store_true", help="запустить LLM-судью")
    parser.add_argument("--judge-parallel", type=int, default=4)
    parser.add_argument("--judge-votes", type=int, default=3, help="оценок судьи на ответ")
    parser.add_argument("--out-report", type=Path, help="копия report.md, например в docs/reports/")
    return asyncio.run(score_run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
