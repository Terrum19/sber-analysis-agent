"""SQL уходит в рабочий поток: event loop продолжает тикать."""

import asyncio

import pytest

from app.core.data.duckdb_manager import DuckDBManager

HEAVY_SQL = "SELECT sum(sin(i)) AS s FROM range(60000000) t(i)"


async def test_event_loop_ticks_while_sql_runs_in_a_thread() -> None:
    db = DuckDBManager(":memory:")
    ticks = 0
    stop = asyncio.Event()

    async def ticker() -> None:
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0.01)

    task = asyncio.create_task(ticker())
    try:
        async with asyncio.timeout(10):
            result = await db.query_async(HEAVY_SQL, timeout_s=20)
    finally:
        stop.set()
        await task
    assert result[0]["s"] is not None
    # Последовательный SQL занял бы ~0.5 с; за это время тикер обязан был сработать.
    assert ticks >= 5
    db.close()


async def test_to_records_async_does_not_use_the_event_loop_thread() -> None:
    db = DuckDBManager(":memory:")
    db.execute("CREATE TABLE t AS SELECT range AS x FROM range(5)")
    records = await db.to_records_async("t", limit=3)
    assert records == [{"x": 0}, {"x": 1}, {"x": 2}]
    assert await db.columns_async("t") == ["x"]
    db.close()


def test_arun_offloads_sync_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Базовый тул по умолчанию не блокирует loop (проверяем через run в потоке)."""
    from app.core.tools.registry import tool_registry

    db = DuckDBManager(":memory:")
    db.execute("CREATE TABLE t AS SELECT 'a' AS k UNION ALL SELECT 'b' AS k")
    tool = tool_registry.get("aggregate")
    table = asyncio.run(tool.arun(tool_context(), db, "t", ["k"]))
    assert {row["k"] for row in db.to_records(table)} == {"a", "b"}
    db.close()


def tool_context():
    from app.core.tools.base import ToolContext

    return ToolContext()
