"""Tests for categorical search, intermediate inspection and the WASM bridge."""

import json
from pathlib import Path

import pytest

from app.core.data.duckdb_manager import DuckDBManager
from app.core.sandbox.wasm_runtime import SandboxError, WASMSandbox
from app.core.tools.registry import tool_registry
from app.core.tools.run_wasm_python import RunWasmPythonTool


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


def test_search_categorical_values_takes_limit_from_parameters(db: DuckDBManager) -> None:
    table = tool_registry.get("search_categorical_values").run(
        db, "raw", ["product", "iphone"], limit=1
    )
    assert len(db.to_records(table)) == 1


def test_inspect_temp_table_defaults_to_the_input_table(db: DuckDBManager) -> None:
    table = tool_registry.get("inspect_temp_table").run(db, "raw", [])
    assert db.to_records(table)[0]["row_count"] == 3


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
    class FakeSandbox(WASMSandbox):
        def run(self, code: str, workdir: Path) -> dict:
            assert code == "result = df"
            assert (workdir / "input.csv").read_text().splitlines()[0] == "id,product,amount"
            (workdir / "result.csv").write_text("product,amount\nApple iPhone 14 Pro,250.0\n")
            return {"ok": True, "stdout": ""}

    monkeypatch.setattr("app.core.tools.run_wasm_python.WASMSandbox", FakeSandbox)
    table = RunWasmPythonTool().run(db, "raw", ["result = df", "raw"])
    assert db.to_records(table) == [{"product": "Apple iPhone 14 Pro", "amount": 250.0}]
    assert db.step_code(table) == ("python", "result = df")


def test_run_wasm_python_wraps_scalars_into_output_col(
    db: DuckDBManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeSandbox(WASMSandbox):
        def run(self, code: str, workdir: Path) -> dict:
            assert code.startswith("result = 3")
            assert "'total'" in code
            (workdir / "result.csv").write_text("total\n3\n")
            return {"ok": True, "stdout": ""}

    monkeypatch.setattr("app.core.tools.run_wasm_python.WASMSandbox", FakeSandbox)
    table = RunWasmPythonTool().run(db, "raw", ["result = 3", "raw", "total"])
    assert db.to_records(table) == [{"total": 3}]


def test_run_wasm_python_reports_sandbox_errors(
    db: DuckDBManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeSandbox(WASMSandbox):
        def run(self, code: str, workdir: Path) -> dict:
            raise SandboxError("code must assign the output to a variable named result")

    monkeypatch.setattr("app.core.tools.run_wasm_python.WASMSandbox", FakeSandbox)
    with pytest.raises(SandboxError, match="result"):
        RunWasmPythonTool().run(db, "raw", ["code", "raw"])
