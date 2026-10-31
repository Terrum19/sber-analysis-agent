"""ToolFactory: retries, fix prompt, collisions, store and startup loading."""

import json
from pathlib import Path
from typing import Any

import pytest

from app.config import settings
from app.core.data.duckdb_manager import DuckDBManager
from app.core.tools.registry import ToolRegistry
from app.core.tools.toolgen.factory import (
    ToolCreationError,
    ToolFactory,
    load_generated_tools,
)
from app.core.tools.toolgen.store import ToolStore
from app.models.generated_tool import GeneratedToolSpec


def spec(name: str = "count_by_value", **overrides: Any) -> GeneratedToolSpec:
    payload = {
        "name": name,
        "description": "Count rows by a column",
        "parameters": [{"name": "column", "type": "column"}],
        "sql": "SELECT {column}, COUNT(*) AS cnt FROM {input} GROUP BY {column}",
        "reference_sql": "SELECT {column}, SUM(1) AS cnt FROM {input} GROUP BY {column}",
        "checks": ["SELECT COUNT(*) > 0 AS ok FROM __tool_result"],
        "sample_arguments": [{"name": "column", "value": "region"}],
    }
    payload.update(overrides)
    return GeneratedToolSpec(**payload)


class ScriptedLLM:
    def __init__(self, *answers: GeneratedToolSpec | Exception) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    async def complete_structured(self, system: str, user: str, schema):
        self.prompts.append(user)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def db() -> DuckDBManager:
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE raw AS SELECT * FROM (VALUES ('msk', 10), ('spb', 20), "
        "('msk', 30)) AS t(region, amount)"
    )
    yield manager
    manager.close()


def factory(
    db: DuckDBManager, llm: ScriptedLLM, store: ToolStore | None = None
) -> tuple[ToolFactory, ToolRegistry]:
    registry = ToolRegistry()
    return ToolFactory(db=db, llm=llm, registry=registry, store=store), registry


async def test_invalid_spec_is_fixed_after_feedback(db: DuckDBManager, tmp_path: Path) -> None:
    invalid = spec(sql="SELECT * FROM missing_table", reference_sql=None)
    llm = ScriptedLLM(invalid, spec())
    store = ToolStore(tmp_path)
    tool_factory, registry = factory(db, llm, store)

    created = await tool_factory.create("count regions", "raw")

    assert created.tool.name == "count_by_value"
    assert created.report.reference_checked is True
    assert "missing_table" in llm.prompts[1]
    assert "- count_by_value: sql: table 'missing_table' is not allowed" in llm.prompts[1]
    assert [tool.name for tool in registry.generated_tools()] == ["count_by_value"]
    assert "count_by_value" in registry.describe()
    assert [record["spec"]["name"] for record in store.records()] == ["count_by_value"]


async def test_feasible_false_is_reported_without_registration(
    db: DuckDBManager,
) -> None:
    llm = ScriptedLLM(
        GeneratedToolSpec(feasible=False, reason="нужны данные, которых нет в таблице")
    )
    tool_factory, registry = factory(db, llm, None)

    with pytest.raises(ToolCreationError, match="not feasible"):
        await tool_factory.create("question", "raw")
    assert registry.generated_tools() == []


async def test_exhausted_attempts_leave_registry_unchanged(db: DuckDBManager, monkeypatch) -> None:
    monkeypatch.setattr(settings, "toolgen_attempts", 2)
    llm = ScriptedLLM(
        spec(sql="SELECT * FROM missing_table", reference_sql=None),
        spec(sql="SELECT * FROM other_missing", reference_sql=None),
    )
    tool_factory, registry = factory(db, llm, None)

    with pytest.raises(ToolCreationError, match="after 2 attempts"):
        await tool_factory.create("question", "raw")
    assert registry.generated_tools() == []
    assert len(llm.prompts) == 2


async def test_name_collision_gets_a_fix_prompt(db: DuckDBManager) -> None:
    llm = ScriptedLLM(spec(name="aggregate"), spec(name="count_regions"))
    registry = ToolRegistry()
    from app.core.tools.sql.aggregate import AggregateTool

    registry.register(AggregateTool())
    tool_factory = ToolFactory(db=db, llm=llm, registry=registry, store=None)

    created = await tool_factory.create("question", "raw")

    assert created.tool.name == "count_regions"
    assert "already registered" in llm.prompts[1]


async def test_llm_failure_is_a_tool_creation_error(db: DuckDBManager) -> None:
    tool_factory, registry = factory(db, ScriptedLLM(RuntimeError("LLM timeout")), None)
    with pytest.raises(ToolCreationError, match="LLM timeout"):
        await tool_factory.create("question", "raw")
    assert registry.generated_tools() == []


def test_load_generated_skips_broken_files(tmp_path: Path) -> None:
    store = ToolStore(tmp_path)
    store.save(spec())
    store.directory.mkdir(parents=True, exist_ok=True)
    (store.directory / "broken.json").write_text("{not json", encoding="utf-8")
    registry = ToolRegistry()

    loaded = load_generated_tools(registry, store)

    assert loaded == ["count_by_value"]
    assert "count_by_value" in registry.names()
    assert [tool.name for tool in registry.generated_tools()] == ["count_by_value"]


def test_store_writes_atomically_and_has_metadata(tmp_path: Path) -> None:
    store = ToolStore(tmp_path)
    path = store.save(spec())
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["spec"]["name"] == "count_by_value"
    assert payload["created_at"]
    assert payload["verified_at"]
    assert not list(store.directory.glob("*.tmp"))
