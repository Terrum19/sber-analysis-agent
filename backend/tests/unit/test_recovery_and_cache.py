"""Unit tests for retry behavior and state caching."""

import pytest

from app.core.agent.recovery import with_retry
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory.state_cache import StateCache


@pytest.mark.asyncio
async def test_with_retry_uses_exponential_delays(monkeypatch) -> None:
    calls = 0
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("temporary")
        return "ok"

    monkeypatch.setattr("app.core.agent.recovery.asyncio.sleep", sleep)
    assert await with_retry(flaky, attempts=3) == "ok"
    assert calls == 3
    assert delays == [1, 2]


@pytest.mark.asyncio
async def test_with_retry_raises_last_error(monkeypatch) -> None:
    async def sleep(delay: float) -> None:
        return None

    async def broken() -> None:
        raise ValueError("broken")

    monkeypatch.setattr("app.core.agent.recovery.asyncio.sleep", sleep)
    with pytest.raises(ValueError, match="broken"):
        await with_retry(broken, attempts=2)


def test_state_cache_persists_step_result() -> None:
    db = DuckDBManager(":memory:")
    db.execute("CREATE TABLE raw AS SELECT * FROM (VALUES (1), (2)) AS rows(value)")
    cache = StateCache(db)
    table = cache.put("raw", "SELECT value * 2 AS doubled FROM raw")
    assert table == "temp_result_v1"
    assert cache.get(table) == [{"doubled": 2}, {"doubled": 4}]
    assert db.query(f"SELECT * FROM {table}") == [{"doubled": 2}, {"doubled": 4}]
    db.close()
