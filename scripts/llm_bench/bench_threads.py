#!/usr/bin/env python3
"""Скорость Ollama в зависимости от num_thread (и num_ctx).

На гибридных CPU (P/E/LP-E ядра) llama.cpp по умолчанию берёт все ядра,
и медленные ядра тормозят остальные на барьерах. Скрипт меряет prefill
и генерацию отдельно на коротком и длинном промпте.

    python bench_threads.py --threads 6 8 14 16 --out threads.jsonl
"""

import argparse
import json
import time
import urllib.request
from pathlib import Path

SHORT = "Напиши SQL: число событий по месяцам из таблицы events(event_date DATE). Только SQL."
# ~1.5k токенов: повторяем описание, чтобы мерить prefill без кеша.
LONG_UNIT = (
    "- group_by [transform]: Сгруппировать по одной или нескольким колонкам (by) и "
    "посчитать агрегат function(column); arguments={by: list[column], column: column}\n"
)


def chat(base: str, model: str, prompt: str, options: dict) -> dict:
    payload = {
        "model": model,
        "stream": False,
        "options": options,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        f"{base}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=3600) as resp:
        d = json.loads(resp.read())
    return {
        "wall_s": time.time() - t0,
        "load_s": d.get("load_duration", 0) / 1e9,
        "prompt_tokens": d.get("prompt_eval_count"),
        "prefill_tps": d.get("prompt_eval_count", 0)
        / max(d.get("prompt_eval_duration", 1) / 1e9, 1e-9),
        "gen_tokens": d.get("eval_count"),
        "gen_tps": d.get("eval_count", 0) / max(d.get("eval_duration", 1) / 1e9, 1e-9),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:11434")
    ap.add_argument("--model", default="qwen2.5-coder-32k")
    ap.add_argument("--threads", type=int, nargs="+", default=[6, 8, 14, 16])
    ap.add_argument("--num-ctx", type=int, nargs="+", default=[32768])
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    for num_ctx in args.num_ctx:
        for threads in args.threads:
            opts = {
                "temperature": 0,
                "num_thread": threads,
                "num_ctx": num_ctx,
                "num_predict": 64,
            }
            # Прогрев: загрузка раннера с новыми параметрами не идёт в замер.
            chat(args.base, args.model, "ok", opts | {"num_predict": 1})
            for kind, prompt in (
                ("short", SHORT),
                ("long", LONG_UNIT * 30 + "\nКратко: что делает group_by?"),
            ):
                # Уникальный префикс — чтобы KV-кеш прошлого запроса не помог.
                res = chat(args.base, args.model, f"[{time.time()}] {prompt}", opts)
                res |= {"num_thread": threads, "num_ctx": num_ctx, "prompt": kind}
                with args.out.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(res) + "\n")
                print(json.dumps(res), flush=True)


if __name__ == "__main__":
    main()
