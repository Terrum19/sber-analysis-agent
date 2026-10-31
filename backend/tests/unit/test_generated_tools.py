"""Deklarative generated tools: Args model, SQL rendering, WASM-only Python."""

import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from app.core.data.duckdb_manager import DuckDBManager
from app.core.tools.base import ToolContext
from app.core.tools.generated import (
    GeneratedPythonTool,
    GeneratedSqlTool,
    build_args_model,
    render_sql,
)
from app.models.generated_tool import GeneratedToolSpec


def sql_spec(**overrides: Any) -> GeneratedToolSpec:
    payload = {
        "name": "count_by_value",
        "description": "Count rows by a column",
        "kind": "transform",
        "language": "sql",
        "parameters": [
            {"name": "column", "type": "column", "description": "column to group by"},
            {"name": "limit", "type": "integer", "required": False, "default": 10},
        ],
        "sql": (
            "SELECT {column}, COUNT(*) AS cnt FROM {input} "
            "GROUP BY {column} ORDER BY cnt DESC LIMIT {limit}"
        ),
        "reference_sql": (
            "SELECT {column}, SUM(1) AS cnt FROM {input} "
            "GROUP BY {column} ORDER BY cnt DESC LIMIT {limit}"
        ),
        "checks": ["SELECT COUNT(*) > 0 AS ok FROM __tool_result"],
        "sample_arguments": [{"name": "column", "value": "region"}],
    }
    payload.update(overrides)
    return GeneratedToolSpec(**payload)


def python_spec(**overrides: Any) -> GeneratedToolSpec:
    payload = {
        "name": "python_count",
        "description": "Count rows in Python",
        "kind": "analysis",
        "language": "python",
        "parameters": [{"name": "column", "type": "column"}],
        "code": 'result = df[[params["column"]]].head(2).copy()',
        "checks": ["SELECT COUNT(*) > 0 AS ok FROM __tool_result"],
        "sample_arguments": [{"name": "column", "value": "region"}],
    }
    payload.update(overrides)
    return GeneratedToolSpec(**payload)


class FakeSandbox:
    """WASM sandbox double: produces a fixed result.csv without running code."""

    def __init__(self, csv: str = "value\n1\n2\n") -> None:
        self.csv = csv
        self.code = ""

    def available(self) -> bool:
        return True

    @contextmanager
    def workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            yield Path(directory)

    def run(self, code: str, workdir: Path) -> dict:
        self.code = code
        (Path(workdir) / "result.csv").write_text(self.csv, encoding="utf-8")
        return {"ok": True}


@pytest.fixture
def db() -> DuckDBManager:
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE raw AS SELECT * FROM (VALUES ('msk', 10), ('spb', 20), "
        "('msk', 30)) AS t(region, amount)"
    )
    yield manager
    manager.close()


def test_args_model_has_types_defaults_and_signature() -> None:
    spec = sql_spec(
        parameters=[
            {"name": "column", "type": "column"},
            {"name": "limit", "type": "integer", "required": False, "default": 5},
            {
                "name": "mode",
                "type": "enum",
                "required": False,
                "default": "sum",
                "values": ["sum", "mean"],
            },
        ]
    )
    tool = GeneratedSqlTool(spec)
    args = tool.parse_args(arguments={"column": "region"})
    assert args.column == "region"
    assert args.limit == 5
    assert args.mode == "sum"
    assert "limit: int = 5" in tool.signature()
    assert "count_by_value [transform]" in tool.describe()
    with pytest.raises(ValueError, match="column"):
        tool.parse_args(arguments={})


def test_args_model_is_dynamic_and_forbids_extra() -> None:
    spec = sql_spec()
    model = build_args_model(spec)
    assert set(model.model_fields) == {"column", "limit"}
    with pytest.raises(ValueError):
        model.model_validate({"column": "region", "unknown": 1})


def test_generated_sql_tool_saves_step_result(db: DuckDBManager) -> None:
    tool = GeneratedSqlTool(sql_spec())
    table = tool.run(db, "raw", column="region")
    assert table.startswith("temp_result_v")
    assert db.to_records(table) == [
        {"region": "msk", "cnt": 2},
        {"region": "spb", "cnt": 1},
    ]


def test_generated_sql_quotes_cyrillic_spaces_and_quotes(db: DuckDBManager) -> None:
    db.execute(
        'CREATE TABLE "странная таблица" AS SELECT \'мск\' AS "колонка с пробелом", 1 AS "a""b"'
    )
    spec = sql_spec(
        parameters=[{"name": "column", "type": "column"}],
        sql="SELECT {column}, COUNT(*) AS cnt FROM {input} GROUP BY {column}",
        reference_sql=None,
        sample_arguments=[{"name": "column", "value": "колонка с пробелом"}],
    )
    table = GeneratedSqlTool(spec).run(db, "странная таблица", column="колонка с пробелом")
    assert db.to_records(table) == [{"колонка с пробелом": "мск", "cnt": 1}]
    # Значение аргумента — identifier, а не SQL-фрагмент.
    rendered = render_sql(spec, "странная таблица", {"column": 'a"b'})
    assert '"a""b"' in rendered


def test_string_argument_may_contain_braces() -> None:
    from app.core.tools.generated import render_parameter
    from app.models.generated_tool import ToolParameterSpec

    parameter = ToolParameterSpec(name="value", type="string")
    assert render_parameter(parameter, "a{b}") == "'a{b}'"


def test_generated_sql_rechecks_rendered_sql_at_runtime(db: DuckDBManager) -> None:
    spec = sql_spec(reference_sql=None, checks=[])
    tool = GeneratedSqlTool(spec)
    tool.spec = spec.model_copy(update={"sql": "SELECT * FROM other_table"})
    with pytest.raises(ValueError, match="other_table"):
        tool.run(db, "raw", column="region")


async def test_generated_python_runs_only_through_sandbox(db: DuckDBManager) -> None:
    sandbox = FakeSandbox()
    tool = GeneratedPythonTool(python_spec())
    table = await tool.arun(ToolContext(sandbox=sandbox), db, "raw", column="region")
    assert db.to_records(table) == [{"value": 1}, {"value": 2}]
    assert sandbox.code.startswith("params = ")
    assert "'column': 'region'" in sandbox.code
    with pytest.raises(TypeError, match="arun"):
        tool.execute(db, "raw", tool.parse_args(arguments={"column": "region"}))


async def test_generated_python_rechecks_code_at_runtime(db: DuckDBManager) -> None:
    spec = python_spec()
    tool = GeneratedPythonTool(spec)
    tool.spec = spec.model_copy(update={"code": "import os\nresult = df"})
    with pytest.raises(ValueError, match="import 'os' is not allowed"):
        await tool.arun(ToolContext(sandbox=FakeSandbox()), db, "raw", column="region")


def test_spec_validation_rejects_wrong_language_shape() -> None:
    with pytest.raises(ValueError, match="SQL tool must not contain code"):
        GeneratedToolSpec(
            name="bad_tool",
            description="Bad",
            language="sql",
            sql="SELECT 1",
            code="result = 1",
            checks=["SELECT true AS ok FROM __tool_result"],
        )
    with pytest.raises(ValueError, match="Python tool requires code"):
        GeneratedToolSpec(name="bad_tool", description="Bad", language="python")
