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


SYNTHETIC_COLUMNS = (
    ("amount", "CASE WHEN range % 17 = 0 THEN NULL ELSE random() * 1000 END"),
    ("count", "(range * 7919 + {index}) % 100000"),
    ("price", "CAST(random() * 10000 AS DECIMAL(12, 2))"),
    ("category", "'cat_' || ((range + {index}) % 50)"),
    ("code", "'id_' || range"),
    ("day", "DATE '2024-01-01' + CAST((range + {index}) % 366 AS INTEGER)"),
    ("moment", "TIMESTAMP '2024-01-01' + range * INTERVAL 1 SECOND"),
    ("flag", "(range + {index}) % 3 = 0"),
)


def synthetic_sql(rows: int, columns: int) -> str:
    expressions = []
    for index in range(columns):
        name, template = SYNTHETIC_COLUMNS[index % len(SYNTHETIC_COLUMNS)]
        expressions.append(f"{template.format(index=index)} AS {name}_{index}")
    return f"SELECT {', '.join(expressions)} FROM range({rows})"


def sources(db, data_dir: Path, synthetic_rows: int, synthetic_columns: int) -> list[str]:
    tables = []
    for path in sorted(data_dir.glob("*.csv")):
        db.attach_csv(path.stem, str(path))
        tables.append(path.stem)
    if synthetic_rows and synthetic_columns:
        db.execute(
            "CREATE OR REPLACE TABLE synthetic_wide AS "
            + synthetic_sql(synthetic_rows, synthetic_columns)
        )
        tables.append("synthetic_wide")
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

    parser = argparse.ArgumentParser(description="Бенчмарк профилирования таблиц (profile_table)")
    default_data = ROOT / "data" / "sample_datasets"
    parser.add_argument("--data-dir", type=Path, default=default_data)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--synthetic-rows", type=int, default=1_000_000)
    parser.add_argument("--synthetic-columns", type=int, default=50)
    parser.add_argument("--max-seconds", type=float, default=None)
    parser.add_argument("--output", type=Path, default=Path("profile_benchmark.json"))
    arguments = parser.parse_args()

    db = DuckDBManager(":memory:")
    try:
        tables = sources(
            db, arguments.data_dir, arguments.synthetic_rows, arguments.synthetic_columns
        )
        results = [measure(db, table, arguments.repeats) for table in tables]
    finally:
        db.close()

    report = {"repeats": arguments.repeats, "results": results}
    arguments.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(render(results))
    failed = any("error" in result for result in results)
    if arguments.max_seconds is not None:
        limit_ms = arguments.max_seconds * 1000
        slow = [result for result in results if result.get("median_ms", 0) > limit_ms]
        for result in slow:
            print(f"Превышен лимит {arguments.max_seconds} с: {result['table']}")
        failed = failed or bool(slow)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
