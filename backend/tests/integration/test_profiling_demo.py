import csv
import json
import statistics
from datetime import date
from pathlib import Path

import pytest

from app.config import settings
from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.profiling import profile_table
from app.core.data.schema_enrichment import SchemaEnricher

DEMO_EVENTS = Path(__file__).resolve().parents[3] / "data" / "sample_datasets" / "demo_events.csv"


class FakeLLM:
    async def complete(self, system: str, prompt: str) -> str:
        return "aliases"


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


async def test_demo_events_passport_saved(db, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    await SchemaEnricher(FakeLLM()).enrich(db, "demo_events")
    saved = json.loads((tmp_path / "profiles" / "demo_events.json").read_text())
    names = [column["name"] for column in saved["profile"]["columns"]]
    assert names == ["event_id", "user_id", "amount", "event_date"]
    assert "amount" in saved["outliers"]
