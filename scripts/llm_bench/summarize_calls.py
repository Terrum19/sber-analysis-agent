#!/usr/bin/env python3
"""Сводка по логу record_proxy.py: какие вызовы, сколько токенов и времени.

Вызов классифицируется по промпту (планировщик / переспрос плана /
исправление шага / инсайт / кодогенерация / прочее) и по форме запроса
(tools / list-tools / json / json_schema). Для ответа отмечается форма:
1 tool_call, N tool_calls, текст.

    python summarize_calls.py calls.jsonl [--markdown]
"""

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path


def kind_of(user: str) -> str:
    if user.startswith("Ты планируешь исследование данных шагами."):
        return "plan_fix" if "Твой предыдущий план" in user else "plan"
    if user.startswith("Шаг исследования данных упал"):
        return "step_fix"
    if user.startswith("На основе результата исследования"):
        return "insight"
    if "Скажи просто: привет" in user:
        return "llm_check"
    return "other"


def request_mode(req: dict) -> str:
    if req.get("tools"):
        names = [t["function"]["name"] for t in req["tools"]]
        return "tools:" + ",".join(names)
    fmt = req.get("response_format") or {}
    return fmt.get("type", "text")


def response_shape(resp) -> str:
    if not isinstance(resp, dict) or "choices" not in resp:
        return "error"
    msg = resp["choices"][0]["message"]
    calls = msg.get("tool_calls") or []
    if calls:
        return f"tool_calls×{len(calls)}"
    return "text"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("log", type=Path)
    args = ap.parse_args()
    rows = []
    for line in args.log.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        req = rec.get("request") or {}
        msgs = req.get("messages") or []
        user = next((m["content"] for m in msgs if m.get("role") == "user"), "")
        resp = rec.get("response")
        usage = resp.get("usage", {}) if isinstance(resp, dict) else {}
        rows.append({
            "id": rec["id"], "kind": kind_of(user), "mode": request_mode(req),
            "shape": response_shape(resp), "seconds": rec.get("seconds", 0),
            "pt": usage.get("prompt_tokens"), "ct": usage.get("completion_tokens"),
            "finish": (resp or {}).get("choices", [{}])[0].get("finish_reason")
            if isinstance(resp, dict) else None,
            "disconnected": rec.get("client_disconnected", False),
            "chars": sum(len(m.get("content") or "") for m in msgs)
            + len(json.dumps(req.get("tools") or [])),
        })

    print("| # | вид | запрос | ответ | finish | prompt tok | compl tok | сек | клиент ушёл |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['id']} | {r['kind']} | {r['mode'][:40]} | {r['shape']} | {r['finish']} | "
              f"{r['pt']} | {r['ct']} | {r['seconds']:.0f} | {'да' if r['disconnected'] else ''} |")

    by_kind = defaultdict(list)
    for r in rows:
        by_kind[r["kind"]].append(r)
    print("\n| вид | вызовов | сек всего | сек медиана | prompt tok медиана | compl tok медиана |")
    print("|---|---|---|---|---|---|")
    for kind, items in by_kind.items():
        pts = [r["pt"] for r in items if r["pt"]]
        cts = [r["ct"] for r in items if r["ct"]]
        print(f"| {kind} | {len(items)} | {sum(r['seconds'] for r in items):.0f} | "
              f"{statistics.median(r['seconds'] for r in items):.0f} | "
              f"{statistics.median(pts) if pts else '—'} | {statistics.median(cts) if cts else '—'} |")
    total = sum(r["seconds"] for r in rows)
    wasted = sum(r["seconds"] for r in rows if r["disconnected"])
    print(f"\nВсего вызовов: {len(rows)}, время LLM: {total:.0f} с, "
          f"из них клиент не дождался: {wasted:.0f} с")


if __name__ == "__main__":
    main()
