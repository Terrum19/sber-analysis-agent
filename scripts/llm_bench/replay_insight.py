#!/usr/bin/env python3
"""Повтор стадии вывода на таблицах готового прогона.

Берёт ходы из sessions.sqlite прогона (вопрос, шаги, result_table) и их
таблицы из main.duckdb (копию) и K раз строит вывод той версией
InsightGenerator, что лежит в --backend. Так разные версии вывода
сравниваются на одних и тех же таблицах, без шума от планов.

    LLM_BASE_URL=... LLM_MODEL=... python scripts/llm_bench/replay_insight.py \\
        results/<прогон>/run out.json --repeats 3 --backend <worktree>/backend
Оценка: grade_insight.py old.json new.json.
"""

import argparse
import asyncio
import inspect
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", type=Path, help="каталог run/ прогона run_dialog.sh")
    parser.add_argument("out", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--backend", type=Path, default=Path(__file__).parents[2] / "backend")
    parser.add_argument("--session", default="cli-session")
    args = parser.parse_args()

    work = Path(tempfile.mkdtemp())
    shutil.copy(args.run / "main.duckdb", work / "main.duckdb")
    os.environ.setdefault("SQLITE_PATH", str(work / "sessions.sqlite"))
    sys.path.insert(0, str(args.backend.resolve()))
    from app.core.agent.insight import InsightGenerator
    from app.core.data.duckdb_manager import DuckDBManager
    from app.models.research_step import ResearchStep

    row = (
        sqlite3.connect(args.run / "sessions.sqlite")
        .execute("SELECT state FROM session_state WHERE session_id = ?", (args.session,))
        .fetchone()
    )
    turns = json.loads(row[0])["turns"]
    db = DuckDBManager(str(work / "main.duckdb"))
    gen = InsightGenerator(db=db)
    takes_steps = "steps" in inspect.signature(gen.generate).parameters

    async def replay() -> list[dict]:
        results = []
        for turn in turns:
            table = turn.get("result_table")
            if not table:
                continue
            kwargs = {}
            if takes_steps:
                kwargs["steps"] = [ResearchStep.model_validate(s) for s in turn["steps"]]
            headlines = []
            for _ in range(args.repeats):
                try:
                    insight = await gen.generate(turn["question"], table, **kwargs)
                    headlines.append(insight.headline)
                except Exception as exc:  # noqa: BLE001 — ход в отчёт, повтор идёт дальше
                    headlines.append(f"ERROR {exc!r}"[:200])
            count = db.query(f'SELECT COUNT(*) AS n FROM "{table}"')[0]["n"]
            results.append(
                {
                    "question": turn["question"],
                    "result_table": table,
                    "row_count": count,
                    "rows": db.to_records(table, limit=50),
                    "headlines": headlines,
                }
            )
            print(turn["question"], headlines, flush=True)
        return results

    args.out.write_text(
        json.dumps(asyncio.run(replay()), ensure_ascii=False, default=str, indent=1),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
