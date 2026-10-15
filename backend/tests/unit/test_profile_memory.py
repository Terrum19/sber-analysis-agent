import json

import pytest

from app.config import settings
from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.schema_enrichment import SchemaEnricher
from app.core.memory.profile_memory import ProfileMemory


class FakeLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(self, system: str, prompt: str) -> str:
        self.prompts.append(prompt)
        return "aliases"


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    return tmp_path


@pytest.fixture
def db():
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE sales AS SELECT * FROM (VALUES "
        "(1.0, 'msk', DATE '2024-01-05'), (2.0, 'spb', DATE '2024-02-05'), "
        "(2.0, NULL, DATE '2024-03-05'), (3.0, 'msk', DATE '2024-04-05'), "
        "(100.0, 'msk', DATE '2024-05-05')) AS rows(amount, region, day)"
    )
    yield manager
    manager.close()


async def test_enrich_saves_full_profile(storage, db) -> None:
    llm = FakeLLM()
    passport = await SchemaEnricher(llm).enrich(db, "sales", glossary={"amount": "сумма"})
    saved = json.loads((storage / "profiles" / "sales.json").read_text())
    assert saved == passport
    assert saved["aliases_raw"] == "aliases"
    assert saved["glossary"] == {"amount": "сумма"}
    assert saved["profile"]["row_count"] == 5
    amount = saved["profile"]["columns"][0]
    assert amount["name"] == "amount"
    assert amount["q1"] == 2.0
    assert amount["q3"] == 3.0
    assert saved["profile"]["columns"][2]["max_date"] == "2024-05-05"
    assert saved["outliers"]["amount"]["count"] == 1
    assert "- region: VARCHAR (text), пропусков 20%" in llm.prompts[0]
    assert ProfileMemory().load("sales") == saved


def test_save_keeps_plain_passports(storage) -> None:
    memory = ProfileMemory()
    record = memory.save("raw", {"profile": {"table": "raw"}, "outliers": {}})
    assert record == {"profile": {"table": "raw"}, "outliers": {}}
    assert memory.load("raw") == record
    assert memory.load("missing") == {}
