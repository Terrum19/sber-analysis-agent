import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))


def profile(db, table: str, runner: asyncio.Runner | None):
    from app.config import settings
    from app.core.data.profiling import profile_table
    from app.core.data.schema_enrichment import SchemaEnricher
    from app.core.memory.profile_memory import ProfileMemory

    if runner is None:
        with db.lock:
            result = profile_table(db, table, approx=settings.profile_approx_quantile)
            ProfileMemory(db).save_profile(result)
        return result, []
    enricher = SchemaEnricher(db)
    rows = runner.run(enricher.enrich(table))
    described = sum(1 for row in rows if row.description)
    notes = [] if enricher.llm_available else ["LLM недоступна"]
    if described < len(rows):
        notes.append(f"описаний {described}/{len(rows)}")
    return ProfileMemory(db).load_profile(table), notes


def load(db, path: Path, runner: asyncio.Runner | None) -> dict:
    from app.core.data.connectors.csv import CSVConnector, table_name

    result = {"file": path.name, "table": table_name(path.name), "notes": []}
    try:
        started = time.perf_counter()
        connector = CSVConnector({result["table"]: str(path)})
        result["table"] = connector.connect(db)[0]
        result["load_s"] = round(time.perf_counter() - started, 1)
        report = connector.reports[0]
        if report.all_text:
            result["notes"].append("все колонки VARCHAR")
        if report.tolerant:
            result["notes"].append("часть строк пропущена/дополнена")
        started = time.perf_counter()
        table_profile, notes = profile(db, result["table"], runner)
        result["profile_s"] = round(time.perf_counter() - started, 1)
        result["rows"] = table_profile.row_count
        result["columns"] = table_profile.column_count
        result["notes"] += notes
    except Exception as error:  # noqa: BLE001
        result["notes"].insert(0, f"{type(error).__name__}: {error}".splitlines()[0])
        result["failed"] = True
    return result


def render(results: list[dict]) -> str:
    lines = [
        "| Файл | Таблица | Строк | Колонок | Загрузка, с | Профиль, с | Ошибки |",
        "|---|---|---|---|---|---|---|",
    ]
    for result in results:
        cells = [result["file"], f"`{result['table']}`"]
        cells += [str(result.get(key, "")) for key in ("rows", "columns", "load_s", "profile_s")]
        cells.append("; ".join(result["notes"]))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> int:
    import duckdb
    from app.config import settings
    from app.core.data.duckdb_manager import DuckDBManager

    parser = argparse.ArgumentParser(description="Загрузка и профилирование датасетов хакатона")
    parser.add_argument("--dir", type=Path, default=ROOT / "data" / "hk_datasets")
    parser.add_argument("--db", default=settings.duckdb_path)
    parser.add_argument("--no-llm", action="store_true", help="только профиль, без описаний LLM")
    arguments = parser.parse_args()

    files = sorted(arguments.dir.glob("*.csv"))
    if not files:
        print(f"В {arguments.dir} нет CSV, запустите make hk-data", file=sys.stderr)
        return 1
    try:
        db = DuckDBManager(arguments.db)
    except duckdb.IOException as error:
        print(f"Не открыть {arguments.db} (backend запущен?): {error}", file=sys.stderr)
        return 1
    runner = None if arguments.no_llm else asyncio.Runner()
    try:
        results = []
        for path in files:
            results.append(load(db, path, runner))
            print(f"{path.name}: готово", file=sys.stderr)
    finally:
        if runner is not None:
            runner.close()
        db.close()

    print(render(results))
    return 1 if any(result.get("failed") for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
