import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))


def measure(db, table: str, repeats: int) -> dict:
    from app.core.data.profiling import profile_table

    timings = []
    try:
        for _ in range(repeats):
            started = time.perf_counter()
            profile = profile_table(db, table)
            timings.append((time.perf_counter() - started) * 1000)
    except Exception as error:
        message = f"{type(error).__name__}: {error}".splitlines()[0]
        return {"table": table, "error": message}
    return {
        "table": table,
        "rows": profile.row_count,
        "columns": profile.column_count,
        "median_ms": round(statistics.median(timings), 2),
        "min_ms": round(min(timings), 2),
        "max_ms": round(max(timings), 2),
    }


def sources(db, data_dir: Path, synthetic_rows: int) -> list[str]:
    tables = []
    for path in sorted(data_dir.glob("*.csv")):
        db.attach_csv(path.stem, str(path))
        tables.append(path.stem)
    if synthetic_rows:
        db.execute(
            "CREATE OR REPLACE TABLE synthetic_numeric AS "
            "SELECT range AS id, random() * 1000 AS amount, "
            "(range % 97)::DOUBLE AS bucket "
            f"FROM range({synthetic_rows})"
        )
        tables.append("synthetic_numeric")
    return tables


def render(results: list[dict]) -> str:
    lines = [
        "| Таблица | Строк | Колонок | Медиана, мс | Мин, мс | Макс, мс |",
        "|---|---|---|---|---|---|",
    ]
    for result in results:
        if "error" in result:
            lines.append(f"| {result['table']} | ❌ {result['error']} | | | | |")
        else:
            lines.append(
                f"| {result['table']} | {result['rows']} | {result['columns']} | "
                f"{result['median_ms']} | {result['min_ms']} | {result['max_ms']} |"
            )
    return "\n".join(lines)


def main() -> int:
    from app.core.data.duckdb_manager import DuckDBManager

    parser = argparse.ArgumentParser(
        description="Бенчмарк профилирования таблиц (profile_table)"
    )
    default_data = ROOT / "data" / "sample_datasets"
    parser.add_argument("--data-dir", type=Path, default=default_data)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--synthetic-rows", type=int, default=1_000_000)
    parser.add_argument("--output", type=Path, default=Path("profile_benchmark.json"))
    arguments = parser.parse_args()

    db = DuckDBManager(":memory:")
    try:
        tables = sources(db, arguments.data_dir, arguments.synthetic_rows)
        results = [measure(db, table, arguments.repeats) for table in tables]
    finally:
        db.close()

    report = {"repeats": arguments.repeats, "results": results}
    arguments.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(render(results))
    return 1 if any("error" in result for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
