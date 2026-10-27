#!/usr/bin/env python3
"""A/B режимов structured output на реальных промптах планировщика.

Промпты берутся из лога record_proxy.py (вызовы, где user-сообщение —
STEP_PLANNER). Каждый промпт прогоняется в нескольких режимах; для
каждого пишется prefill/генерация (токены и секунды из ответа Ollama),
валиден ли ответ как ResearchPlan, проходит ли validate_plan и какие
тулы выбраны.

Режимы:
  tools        — как сейчас: запрос instructor (TOOLS) из лога как есть,
                 через OpenAI-совместимый /v1 (таймингов prefill нет)
  json_hint    — /api/chat, format="json" + схема текстом в конце промпта
  json_schema  — /api/chat, format=<JSON-схема ResearchPlan> (грамматика)
  json_schema_topk — json_schema, но в промпте только релевантные тулы

Запуск (из backend/, интерпретатор проекта):
    USE_FAKE_LLM=true python ../scripts/llm_bench/bench_structured.py \
        --calls calls.jsonl --modes tools json_schema --out bench.jsonl
"""

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.agent.planner import validate_plan  # noqa: E402
from app.core.tools.registry import tool_registry  # noqa: E402
from app.models.research_step import ResearchPlan  # noqa: E402

PLANNER_MARK = "Ты планируешь исследование данных шагами."
TOOLS_START = "arguments={имя: тип = значение по умолчанию}):\n"
TOOLS_END = "\n\nРелевантные колонки"

# Тулы, нужные почти любому вопросу: остальные добавляются по словам вопроса.
CORE_TOOLS = (
    "filter_rows", "group_by", "time_bucket", "sort_limit", "aggregate",
    "create_column", "select_columns", "describe_stats", "search_categorical_values",
)

# Что должен содержать разумный план (грубая метрика качества).
EXPECTED = {
    "Какая общая сумма amount?": [{"aggregate", "group_by", "describe_stats"}],
    "Сколько событий по месяцам?": [{"time_bucket", "group_by"}],
    "Средняя сумма события по месяцам": [{"time_bucket", "group_by"}],
    "Топ-5 пользователей по сумме amount": [{"group_by"}, {"sort_limit"}],
    "Сколько событий с суммой больше 150?": [{"filter_rows"}, {"aggregate", "group_by"}],
    "Есть ли выбросы в amount?": [{"describe_stats", "bootstrap_ci", "shapiro_wilk"}],
    "Как менялась выручка во времени, есть ли тренд?": [
        {"trend_analysis", "time_bucket", "linear_regression", "revenue_analysis"}
    ],
}


def planner_prompts(calls_path: Path) -> list[dict]:
    """Уникальные первые запросы планировщика (по вопросу) из лога прокси."""
    seen, out = set(), []
    for line in calls_path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        req = rec.get("request") or {}
        msgs = req.get("messages") or []
        user = next((m["content"] for m in msgs if m.get("role") == "user"), "")
        if not user.startswith(PLANNER_MARK):
            continue
        question = re.search(r"Вопрос пользователя: (.*)", user).group(1).strip()
        if question in seen or "Твой предыдущий план" in user:
            continue
        seen.add(question)
        out.append({"question": question, "request": req, "user": user,
                    "system": next(m["content"] for m in msgs if m["role"] == "system")})
    return out


def _stem(word: str) -> str:
    return word.lower()[:5]


def topk_tools(user: str, question: str) -> str:
    """Оставить в промпте CORE_TOOLS + тулы, чьё имя/описание пересекается с вопросом."""
    start = user.index(TOOLS_START) + len(TOOLS_START)
    end = user.index(TOOLS_END)
    lines = user[start:end].split("\n")
    stems = {_stem(w) for w in re.findall(r"\w{4,}", question)}
    kept = []
    for line in lines:
        name = line[2:].split(" ", 1)[0]
        words = {_stem(w) for w in re.findall(r"\w{4,}", line.split(";", 1)[0])}
        if name in CORE_TOOLS or stems & words:
            kept.append(line)
    return user[:start] + "\n".join(kept) + user[end:]


def post(url: str, payload: dict, timeout: float = 1800) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def run_native(base: str, model: str, system: str, user: str, fmt, num_ctx: int,
               max_tokens: int) -> dict:
    t0 = time.time()
    resp = post(f"{base}/api/chat", {
        "model": model, "stream": False, "format": fmt,
        "options": {"temperature": 0, "num_ctx": num_ctx, "num_predict": max_tokens},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    })
    return {
        "wall_s": time.time() - t0,
        "prompt_tokens": resp.get("prompt_eval_count"),
        "prefill_s": (resp.get("prompt_eval_duration") or 0) / 1e9,
        "gen_tokens": resp.get("eval_count"),
        "gen_s": (resp.get("eval_duration") or 0) / 1e9,
        "load_s": (resp.get("load_duration") or 0) / 1e9,
        "done_reason": resp.get("done_reason"),
        "content": resp["message"].get("content", ""),
    }


def run_tools(base: str, request: dict, max_tokens: int) -> dict:
    payload = dict(request)
    payload["max_tokens"] = max_tokens
    t0 = time.time()
    resp = post(f"{base}/v1/chat/completions", payload)
    msg = resp["choices"][0]["message"]
    calls = msg.get("tool_calls") or []
    usage = resp.get("usage") or {}
    content = calls[0]["function"]["arguments"] if len(calls) == 1 else msg.get("content") or ""
    return {
        "wall_s": time.time() - t0,
        "prompt_tokens": usage.get("prompt_tokens"),
        "gen_tokens": usage.get("completion_tokens"),
        "tool_calls": len(calls),
        "done_reason": resp["choices"][0].get("finish_reason"),
        "content": content,
        "raw_tool_calls": [c["function"] for c in calls],
    }


def judge(question: str, content: str) -> dict:
    """Валидность ответа как ResearchPlan и грубая оценка выбора тулов."""
    out: dict = {"valid_json": False, "valid_schema": False, "plan_errors": None, "tools": []}
    try:
        json.loads(content)
        out["valid_json"] = True
        plan = ResearchPlan.model_validate_json(content)
    except Exception as exc:  # noqa: BLE001
        out["parse_error"] = repr(exc)[:300]
        return out
    out["valid_schema"] = True
    for i, step in enumerate(plan.steps):
        step.step_id = i
    out["tools"] = [s.tool for s in plan.steps]
    out["plan_errors"] = validate_plan(plan)
    expected = EXPECTED.get(question)
    if expected is not None:
        used = set(out["tools"])
        out["expected_ok"] = all(group & used for group in expected)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--calls", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--modes", nargs="+", default=["tools", "json_hint", "json_schema"])
    ap.add_argument("--base", default="http://localhost:11434")
    ap.add_argument("--model", default="qwen2.5-coder-32k")
    ap.add_argument("--num-ctx", type=int, default=32768)
    ap.add_argument("--max-tokens", type=int, default=1024)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0, help="сколько вопросов (0 — все)")
    args = ap.parse_args()

    schema = ResearchPlan.model_json_schema()
    hint = ("\n\nОтветь только JSON-объектом по этой JSON-схеме, без пояснений:\n"
            + json.dumps(schema, ensure_ascii=False))
    prompts = planner_prompts(args.calls)
    if args.limit:
        prompts = prompts[: args.limit]
    print(f"промптов планировщика: {len(prompts)}; тулов в реестре: "
          f"{len(tool_registry.planner_tools())}", flush=True)

    # Порядок: режим снаружи, вопросы внутри — так видно и холодный, и
    # «тёплый» (общий префикс в KV-кеше Ollama) prefill.
    for rep in range(args.repeat):
        for mode in args.modes:
            for item in prompts:
                q, user = item["question"], item["user"]
                if mode.endswith("_topk"):
                    user = topk_tools(user, q)
                try:
                    if mode == "tools":
                        res = run_tools(args.base, item["request"], args.max_tokens)
                    elif mode.startswith("json_hint"):
                        res = run_native(args.base, args.model, item["system"], user + hint,
                                         "json", args.num_ctx, args.max_tokens)
                    elif mode.startswith("json_schema"):
                        res = run_native(args.base, args.model, item["system"], user,
                                         schema, args.num_ctx, args.max_tokens)
                    else:
                        raise SystemExit(f"неизвестный режим {mode}")
                    res |= judge(q, res["content"])
                except Exception as exc:  # noqa: BLE001
                    res = {"error": repr(exc)[:500]}
                res |= {"mode": mode, "question": q, "rep": rep, "prompt_chars": len(user)}
                with args.out.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(res, ensure_ascii=False, default=str) + "\n")
                print(f"[{mode}] {q[:40]!r}: wall={res.get('wall_s', 0):.1f}s "
                      f"pt={res.get('prompt_tokens')} gt={res.get('gen_tokens')} "
                      f"schema={res.get('valid_schema')} errs={res.get('plan_errors')} "
                      f"ok={res.get('expected_ok')} tools={res.get('tools')} "
                      f"{res.get('error', '')}", flush=True)


if __name__ == "__main__":
    main()
