import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))


def describe(
    db, connector, table: str, document: str | None, runner: asyncio.Runner | None
):
    """Перенести документ-описание в паспорт таблицы; замечания для отчёта."""
    from app.core.data import ingest

    if runner is None or document is None:
        return []
    rows = runner.run(ingest.describe(connector, db, [table], document)).get(table, [])
    notes = []
    if any(row.fingerprint is None for row in rows):
        notes.append("LLM не ответила по части колонок")
    described = sum(1 for row in rows if row.description)
    if described < len(rows):
        notes.append(f"описаний {described}/{len(rows)}")
    return notes


def load(db, path: Path, document: str | None, runner: asyncio.Runner | None) -> dict:
    from app.core.data import ingest
    from app.core.data.connectors.csv import CSVConnector, table_name

    result = {"file": path.name, "table": table_name(path.name), "notes": []}
    try:
        started = time.perf_counter()
        connector = CSVConnector({result["table"]: str(path)})
        with db.lock:
            [(result["table"], table_profile)] = ingest.attach(connector, db).items()
        result["load_s"] = round(time.perf_counter() - started, 1)
        report = connector.reports[0]
        if report.all_text:
            result["notes"].append("все колонки VARCHAR")
        if report.tolerant:
            result["notes"].append("часть строк пропущена/дополнена")
        result["rows"] = table_profile.row_count
        result["columns"] = table_profile.column_count
        started = time.perf_counter()
        result["notes"] += describe(db, connector, result["table"], document, runner)
        result["describe_s"] = round(time.perf_counter() - started, 1)
    except Exception as error:  # noqa: BLE001
        result["notes"].insert(0, f"{type(error).__name__}: {error}".splitlines()[0])
        result["failed"] = True
    return result


def render(results: list[dict]) -> str:
    lines = [
        "| Файл | Таблица | Строк | Колонок | Загрузка и профиль, с | Описание, с | Ошибки |",
        "|---|---|---|---|---|---|---|",
    ]
    for result in results:
        cells = [result["file"], f"`{result['table']}`"]
        cells += [
            str(result.get(key, ""))
            for key in ("rows", "columns", "load_s", "describe_s")
        ]
        cells.append("; ".join(result["notes"]))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> int:
    import duckdb
    from app.config import settings
    from app.core.data.duckdb_manager import DuckDBManager

    parser = argparse.ArgumentParser(
        description="Загрузка и профилирование датасетов хакатона"
    )
    parser.add_argument("--dir", type=Path, default=ROOT / "data" / "hk_datasets")
    parser.add_argument("--db", default=settings.duckdb_path)
    parser.add_argument(
        "--description",
        type=Path,
        help="документ-описание колонок (по умолчанию <dir>/description.md, если есть)",
    )
    parser.add_argument(
        "--no-llm", action="store_true", help="только профиль, без описания"
    )
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
    description = arguments.description or arguments.dir / "description.md"
    document = (
        description.read_text(encoding="utf-8") if description.is_file() else None
    )
    runner = None if arguments.no_llm else asyncio.Runner()
    try:
        results = []
        for path in files:
            results.append(load(db, path, document, runner))
            print(f"{path.name}: готово", file=sys.stderr)
    finally:
        if runner is not None:
            runner.close()
        db.close()

    print(render(results))
    return 1 if any(result.get("failed") for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
