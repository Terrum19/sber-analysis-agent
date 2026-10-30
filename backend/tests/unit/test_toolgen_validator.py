"""Toolgen validator: static SQL/Python rules and data-level checks."""

import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from app.core.data.duckdb_manager import DuckDBManager
from app.core.tools.toolgen.validator import (
    ToolValidationError,
    ToolValidator,
    check_rendered_sql,
    static_errors,
)
from app.models.generated_tool import GeneratedToolSpec


def spec(**overrides: Any) -> GeneratedToolSpec:
    payload = {
        "name": "count_by_value",
        "description": "Count rows by a column",
        "parameters": [{"name": "column", "type": "column"}],
        "sql": "SELECT {column}, COUNT(*) AS cnt FROM {input} GROUP BY {column}",
        "reference_sql": "SELECT {column}, SUM(1) AS cnt FROM {input} GROUP BY {column}",
        "checks": ["SELECT COUNT(*) > 0 AS ok FROM __tool_result"],
        "sample_arguments": [{"name": "column", "value": "region"}],
    }
    payload.update(overrides)
    return GeneratedToolSpec(**payload)


@pytest.fixture
def db() -> DuckDBManager:
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE raw AS SELECT * FROM (VALUES ('msk', 10), ('spb', 20), "
        "('msk', 30)) AS t(region, amount)"
    )
    yield manager
    manager.close()


class FakeSandbox:
    def __init__(self, csv: str = "value\n1\n") -> None:
        self.csv = csv

    def available(self) -> bool:
        return True

    @contextmanager
    def workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            yield Path(directory)

    def run(self, code: str, workdir: Path) -> dict:
        (Path(workdir) / "result.csv").write_text(self.csv, encoding="utf-8")
        return {"ok": True}


def test_valid_sql_spec_passes_all_checks(db: DuckDBManager) -> None:
    report = ToolValidator(db).validate(spec(), "raw")
    assert report.sample_rows == 3
    assert report.result_rows == 2
    assert report.reference_checked is True
    assert report.checks_passed == 1
    assert report.elapsed_ms >= 1


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "SELECT {column}, COUNT(*) FROM {input} GROUP BY {column}; DROP TABLE {input}",
            "multiple statements",
        ),
        ("SELECT * FROM read_csv_auto('x.csv')", "read_csv_auto"),
        ("SELECT getenv('HOME') AS x FROM {input}", "getenv"),
        ("SELECT * FROM other_table", "other_table"),
        ("SELECT * FROM {input} USING SAMPLE 10", "SAMPLE"),
        ("SELECT random() AS x FROM {input}", "random"),
        ("CREATE TABLE x AS SELECT 1", "SELECT"),
        ("SELECT * FROM main.{input}", "schema-qualified"),
        ("SELECT * FROM {input} -- comment", "comments"),
    ],
)
def test_forbidden_sql_is_rejected(sql: str, expected: str) -> None:
    errors = static_errors(spec(sql=sql, reference_sql=None))
    assert any(expected in error for error in errors), errors


def test_placeholders_must_be_declared_and_used() -> None:
    errors = static_errors(spec(sql="SELECT {nope} FROM {input}", reference_sql=None))
    assert any("unknown placeholders" in error for error in errors)
    errors = static_errors(spec(sql="SELECT {column} FROM {input}", reference_sql=None))
    assert not errors
    errors = static_errors(spec(sql="SELECT COUNT(*) FROM {input}", reference_sql=None))
    assert any("unused parameters" in error for error in errors)
    errors = static_errors(spec(sql="SELECT {column}", reference_sql=None))
    assert any("input" in error for error in errors)
    errors = static_errors(spec(sql="SELECT {column} FROM {input} WHERE x = {column!r}"))
    assert any("invalid placeholder syntax" in error for error in errors)


def test_empty_result_is_rejected_unless_allowed(db: DuckDBManager) -> None:
    empty = spec(
        sql="SELECT {column}, COUNT(*) AS cnt FROM {input} "
        "WHERE {column} = 'nope' GROUP BY {column}",
        reference_sql=None,
        checks=["SELECT COUNT(*) >= 0 AS ok FROM __tool_result"],
    )
    with pytest.raises(ToolValidationError, match="empty"):
        ToolValidator(db).validate(empty, "raw")
    report = ToolValidator(db).validate(empty.model_copy(update={"allow_empty": True}), "raw")
    assert report.result_rows == 0


def test_missing_column_is_caught_by_explain(db: DuckDBManager) -> None:
    broken = spec(
        sql="SELECT {column}, missing_column FROM {input}",
        reference_sql=None,
        checks=["SELECT COUNT(*) >= 0 AS ok FROM __tool_result"],
    )
    with pytest.raises(ToolValidationError, match="missing_column"):
        ToolValidator(db).validate(broken, "raw")


def test_reference_mismatch_is_rejected(db: DuckDBManager) -> None:
    mismatch = spec(
        reference_sql=("SELECT {column}, COUNT(*) + 1 AS cnt FROM {input} GROUP BY {column}")
    )
    with pytest.raises(ToolValidationError, match="different result"):
        ToolValidator(db).validate(mismatch, "raw")


def test_false_check_is_rejected(db: DuckDBManager) -> None:
    false_check = spec(checks=["SELECT COUNT(*) < 0 AS ok FROM __tool_result"])
    with pytest.raises(ToolValidationError, match="check 1 is false"):
        ToolValidator(db).validate(false_check, "raw")


def test_check_must_read_tool_result() -> None:
    errors = static_errors(spec(checks=["SELECT 1 AS ok"]))
    assert any("must read __tool_result" in error for error in errors)


def test_runtime_rendered_sql_cannot_read_another_table() -> None:
    with pytest.raises(ValueError, match="other_table"):
        check_rendered_sql("SELECT * FROM other_table", allowed_tables={"raw"})


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("result = df[", "syntax error"),
        ("import os\nresult = df", "import 'os'"),
        ("result = open('x')", "call open()"),
        ("x = 1", "assign the result"),
        ("result = df.__class__", "attribute __class__"),
        ("global x\nresult = df", "global/nonlocal"),
    ],
)
def test_forbidden_python_is_rejected(code: str, expected: str) -> None:
    errors = static_errors(
        spec(
            language="python",
            sql=None,
            code=code,
            reference_sql=None,
            checks=["SELECT COUNT(*) > 0 AS ok FROM __tool_result"],
        )
    )
    assert any(expected in error for error in errors), errors


def test_python_spec_runs_in_fake_sandbox(db: DuckDBManager) -> None:
    python = spec(
        name="python_head",
        language="python",
        sql=None,
        code='result = df[[params["column"]]].head(1)',
        reference_sql=None,
        checks=["SELECT COUNT(*) > 0 AS ok FROM __tool_result"],
    )
    report = ToolValidator(db).validate(python, "raw", sandbox=FakeSandbox())
    assert report.result_rows == 1
    assert report.checks_passed == 1


def test_name_collision_is_rejected() -> None:
    errors = static_errors(spec(name="aggregate"), {"aggregate"})
    assert any("already registered" in error for error in errors)
