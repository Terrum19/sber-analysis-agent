"""Кеши планов/результатов: hit/miss, TTL, инвалидация."""

import time

from app.core.cache.models import cache_key, hash_text
from app.core.cache.plan_cache import PlanCache
from app.core.cache.result_cache import CachedResult, ResultCache
from app.models.research_step import ResearchPlan, ResearchStep


def key(question: str = "Сумма", table: str = "sales") -> object:
    return cache_key(
        namespace="plan",
        question=question,
        table=table,
        schema=hash_text("amount"),
        model="m",
    )


def test_plan_cache_hit_miss_and_invalidation() -> None:
    cache = PlanCache()
    plan = ResearchPlan(steps=[ResearchStep(step_id=0, tool="aggregate", arguments={"column": "amount"})])
    assert cache.get(key()) is None
    cache.put(key(), plan, ttl_s=60)
    loaded = cache.get(key())
    assert loaded is not None
    assert loaded.steps[0].arguments["column"] == "amount"
    cache.invalidate("sales")
    assert cache.get(key()) is None


def test_plan_cache_is_isolated_by_question() -> None:
    cache = PlanCache()
    cache.put(key("Сумма"), ResearchPlan(), ttl_s=60)
    assert cache.get(key("Среднее")) is None


def test_plan_cache_ttl() -> None:
    cache = PlanCache()
    cache.put(key(), ResearchPlan(), ttl_s=1)
    entry = next(iter(cache._entries.values()))
    entry.expires_at = time.time() - 1
    assert cache.get(key()) is None


def test_result_cache_round_trip_and_expiry() -> None:
    cache = ResultCache()
    result_key = cache_key(
        namespace="result",
        question="Сумма",
        table="sales",
        schema="s",
        model="m",
    )
    cache.put(result_key, CachedResult(rows=[{"x": 1}], meta={"source": "sql"}), ttl_s=60)
    loaded = cache.get(result_key)
    assert loaded is not None
    assert loaded.rows == [{"x": 1}]
    assert loaded.meta["source"] == "sql"
    cache.invalidate("sales")
    assert cache.get(result_key) is None
