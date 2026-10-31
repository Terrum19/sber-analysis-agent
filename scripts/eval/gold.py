#!/usr/bin/env python3
"""Эталоны eval по сырым CSV организаторов, независимо от загрузчика агента.

Каждый CSV читается DuckDB напрямую (`;`, десятичная запятая) в таблицу с тем же
именем, что у агента (t_01_prodazhi, ...), поэтому SQL в questions.yaml пишется
по именам агента. Результат — data/eval/hk/gold.json: его коммитим, чтобы
эталонные числа было видно в ревью.

    python3 scripts/eval/gold.py --csv-dir data/hk_datasets
"""

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

import duckdb
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
EVAL_DIR = ROOT / "data" / "eval" / "hk"


def load_tables(con: duckdb.DuckDBPyConnection, csv_dir: Path) -> list[str]:
    from app.core.data.connectors.csv import table_name

    tables = []
    for path in sorted(csv_dir.glob("*.csv")):
        name = table_name(path.name)
        con.execute(
            f"CREATE TABLE {name} AS SELECT * FROM read_csv(?, delim=';', "
            "decimal_separator=',', header=true)",
            [str(path.resolve())],
        )
        tables.append(name)
    return tables


def plain(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float):
        return round(value, 6)
    return value


def rows(con, sql: str) -> list[list]:
    return [[plain(value) for value in row] for row in con.execute(sql).fetchall()]


def gold_for(con, expect: dict) -> dict:
    gold = {}
    numbers = expect.get("numbers")
    if numbers:
        variants = numbers.get("any_of") or [numbers]
        gold["numbers"] = [rows(con, variant["sql"]) for variant in variants]
    if "range" in expect:
        [[low, high]] = rows(con, expect["range"]["sql"])
        gold["range"] = [low, high]
    if "contains_any_sql" in expect:
        gold["contains_any"] = [str(row[0]) for row in rows(con, expect["contains_any_sql"])]
    if expect.get("table_intact"):
        gold["row_count"] = None  # заполняется ниже по таблице задачи
    return gold


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv-dir", type=Path, default=ROOT / "data" / "hk_datasets")
    parser.add_argument("--questions", type=Path, default=EVAL_DIR / "questions.yaml")
    parser.add_argument("--out", type=Path, default=EVAL_DIR / "gold.json")
    args = parser.parse_args()

    con = duckdb.connect()
    tables = load_tables(con, args.csv_dir)
    if len(tables) != 9:
        print(f"Ожидалось 9 CSV в {args.csv_dir}, найдено {len(tables)}", file=sys.stderr)
        return 1
    tasks = yaml.safe_load(args.questions.read_text(encoding="utf-8"))
    result, failed = {}, 0
    for task in tasks:
        if task["dataset"] not in tables:
            print(f"{task['id']}: нет таблицы {task['dataset']}", file=sys.stderr)
            failed += 1
            continue
        try:
            gold = gold_for(con, task.get("expect") or {})
        except duckdb.Error as error:
            print(f"{task['id']}: {error}", file=sys.stderr)
            failed += 1
            continue
        if "row_count" in gold:
            [[gold["row_count"]]] = rows(con, f"SELECT count(*) FROM {task['dataset']}")
        if gold:
            result[task["id"]] = gold
            print(f"{task['id']}: {json.dumps(gold, ensure_ascii=False)}")
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n{len(result)} эталонов -> {args.out}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
