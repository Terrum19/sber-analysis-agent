#!/usr/bin/env python3
"""Планы и ответы по ходам из calls.jsonl прогона: show_turns.py results/<имя>/calls.jsonl.

[reply] — ход-ответ (планировщик получил черновик), [hist] — обычный ход.
"""

import json
import re
import sys


def main() -> None:
    with open(sys.argv[1], encoding="utf-8") as calls:
        lines = calls.readlines()
    for line in lines:
        call = json.loads(line)
        messages = (call.get("request") or {}).get("messages") or []
        prompt = messages[-1]["content"] if messages else ""
        question = re.search(r"Вопрос пользователя: (.+)", prompt)
        if not question or call.get("status") != 200:
            continue
        content = call["response"]["choices"][0]["message"].get("content") or ""
        try:
            parsed = json.loads(content)
        except ValueError:
            parsed = None
        if "Выдай план" in prompt:
            mode = "reply" if "Черновик плана" in prompt else "hist"
            steps = parsed.get("steps", []) if isinstance(parsed, dict) else []
            plan = "; ".join(
                f"{s.get('tool')} {json.dumps(s.get('arguments'), ensure_ascii=False)}"
                for s in steps
            )
            print(f"[{mode}] {question.group(1)}\n  план: {plan or content[:200]}")
        else:
            headline = parsed.get("headline") if isinstance(parsed, dict) else content[:200]
            print(f"  ответ: {headline}")


if __name__ == "__main__":
    main()
