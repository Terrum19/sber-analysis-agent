"""Tests for categorical search, intermediate inspection and the WASM bridge."""

import json

import pytest

from app.core.data.duckdb_manager import DuckDBManager
from app.core.tools.registry import tool_registry


@pytest.fixture
def db() -> DuckDBManager:
    manager = DuckDBManager(":memory:")
    manager.execute(
        """
        CREATE TABLE raw AS SELECT * FROM (
            VALUES
                (1, 'Apple iPhone 14 Pro', 100.0),
                (2, 'Samsung Galaxy S24', 200.0),
                (3, 'Apple iPhone 14 Pro', 150.0)
        ) AS rows(id, product, amount)
        """
    )
    yield manager
    manager.close()


def test_search_categorical_values_returns_ranked_matches(db: DuckDBManager) -> None:
    table = tool_registry.get("search_categorical_values").run(
        db, "raw", ["product", "iphone", "1"]
    )
    row = db.to_records(table)[0]
    assert row["stored_value"] == "Apple iPhone 14 Pro"
    assert float(row["match_score"]) > 0.3


def test_inspect_temp_table_returns_json_passport(db: DuckDBManager) -> None:
    table = tool_registry.get("inspect_temp_table").run(db, "raw", ["raw"])
    row = db.to_records(table)[0]
    assert json.loads(row["schema"]) == ["id", "product", "amount"]
    assert json.loads(row["dtypes"])[0].startswith("INTEGER")
    assert row["row_count"] == 3
    assert json.loads(row["sample_rows"])[0]["product"] == "Apple iPhone 14 Pro"
    assert float(json.loads(row["descriptive_stats"])["amount"]["mean"]) == pytest.approx(150)


def test_run_wasm_python_is_only_a_bridge(
    db: DuckDBManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeSandbox:
        def __init__(self, timeout_s: int) -> None:
            assert timeout_s == 30

        def run(self, *, code: str, payload: dict) -> dict:
            assert code == "return data"
            assert payload["table"] == "raw"
            return {
                "result": [
                    {"product": "Apple iPhone 14 Pro", "amount": 250.0},
                ]
            }

    monkeypatch.setattr("app.core.tools.run_wasm_python.WASMSandbox", FakeSandbox)
    table = tool_registry.get("run_wasm_python").run(db, "raw", ["return data", "raw"])
    assert db.to_records(table) == [{"product": "Apple iPhone 14 Pro", "amount": 250.0}]


def test_run_wasm_python_requires_sandbox_result(
    db: DuckDBManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeSandbox:
        def __init__(self, timeout_s: int) -> None:
            pass

        def run(self, *, code: str, payload: dict) -> dict:
            return {}

    monkeypatch.setattr("app.core.tools.run_wasm_python.WASMSandbox", FakeSandbox)
    with pytest.raises(ValueError, match="result"):
        tool_registry.get("run_wasm_python").run(db, "raw", ["code", "raw"])
