import csv
import json
import statistics
from datetime import date
from pathlib import Path

import pytest

from app.config import settings
from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.profiling import outlier_summary, profile_table
from app.core.data.schema_enrichment import SchemaEnricher
from app.core.memory.profile_memory import ProfileMemory

DEMO_EVENTS = Path(__file__).resolve().parents[3] / "data" / "sample_datasets" / "demo_events.csv"


class FakeLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def complete_structured(self, system: str, prompt: str, schema):
        self.calls += 1
        start = prompt.index("Колонки (JSON):\n") + len("Колонки (JSON):\n")
        columns = json.loads(prompt[start : prompt.index("\n\n", start)])
        return schema.model_validate(
            {
                "columns": [
                    {
                        "column": column["column"],
                        "description": f"Описание {column['column']}",
                        "aliases": [f"{column['column']} (алиас)"],
                    }
                    for column in columns
                ]
            }
        )


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    manager.attach_csv("demo_events", str(DEMO_EVENTS))
    yield manager
    manager.close()


@pytest.fixture
def amounts() -> list[float]:
    with DEMO_EVENTS.open(newline="") as handle:
        return [float(row["amount"]) for row in csv.DictReader(handle)]


def test_demo_events_profile(db, amounts) -> None:
    profile = profile_table(db, "demo_events", approx=False)
    assert profile.row_count == len(amounts)
    amount = profile.column("amount")
    quartiles = statistics.quantiles(amounts, n=4, method="inclusive")
    lower, upper = quartiles[0], quartiles[2]
    assert amount.q1 == pytest.approx(lower)
    assert amount.q3 == pytest.approx(upper)
    assert amount.iqr == pytest.approx(upper - lower)
    assert amount.std == pytest.approx(statistics.pstdev(amounts))
    assert amount.mean == pytest.approx(statistics.fmean(amounts))
    event_date = profile.column("event_date")
    assert event_date.kind == "temporal"
    first, last = event_date.min_date, event_date.max_date
    assert isinstance(first, date)
    assert (last.year - first.year) * 12 + last.month - first.month + 1 == 12
    assert profile.column("event_id").is_unique


async def test_demo_events_passport_saved(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "storage"))
    path = str(tmp_path / "main.duckdb")
    first = DuckDBManager(path)
    first.attach_csv("demo_events", str(DEMO_EVENTS))
    llm = FakeLLM()
    rows = await SchemaEnricher(first, llm).enrich("demo_events")
    before = ProfileMemory(first).load_profile("demo_events")
    first.close()

    assert llm.calls == 1
    assert [row.column for row in rows] == ["event_id", "user_id", "amount", "event_date"]
    assert all(row.description_source == "generated" and row.aliases for row in rows)
    assert not (tmp_path / "storage" / "profiles").exists()

    second = DuckDBManager(path)
    try:
        memory = ProfileMemory(second)
        saved = memory.load_profile("demo_events")
        assert saved == before
        assert saved.row_count == 500
        assert "amount" in outlier_summary(saved)
        assert memory.load_enrichment("demo_events") == rows
        passports = second.query(
            "SELECT table_name, enriched_at IS NOT NULL AS done FROM _meta.table_passport"
        )
        assert passports == [{"table_name": "demo_events", "done": True}]
    finally:
        second.close()
