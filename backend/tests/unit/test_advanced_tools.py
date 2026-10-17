"""Reference tests for bootstrap, funnel, cohort and domain tools."""

import pytest

from app.core.data.duckdb_manager import DuckDBManager
from app.core.tools.registry import tool_registry


@pytest.fixture
def advanced_db() -> DuckDBManager:
    db = DuckDBManager(":memory:")
    db.execute(
        """
        CREATE TABLE events AS SELECT * FROM (
            VALUES
                ('u1', 'landing', TIMESTAMP '2024-01-01 09:00:00', 100.0, 'p1'),
                ('u1', 'signup',  TIMESTAMP '2024-01-01 09:05:00', 120.0, 'p1'),
                ('u1', 'purchase',TIMESTAMP '2024-01-01 09:10:00', 200.0, 'p1'),
                ('u2', 'landing', TIMESTAMP '2024-01-02 09:00:00', 100.0, 'p2'),
                ('u2', 'signup',  TIMESTAMP '2024-01-02 09:05:00', 80.0, 'p2'),
                ('u3', 'landing', TIMESTAMP '2024-02-01 09:00:00', 100.0, 'p1'),
                ('u3', 'signup',  TIMESTAMP '2024-02-01 09:05:00', 90.0, 'p1')
        ) AS rows(user_id, event_type, event_time, amount, product_id)
        """
    )
    db.execute(
        """
        CREATE TABLE retention AS SELECT * FROM (
            VALUES
                ('u1', '2024-01', '2024-01', 100.0),
                ('u1', '2024-01', '2024-02', 120.0),
                ('u2', '2024-01', '2024-01', 80.0),
                ('u3', '2024-02', '2024-02', 90.0)
        ) AS rows(user_id, cohort, period, amount)
        """
    )
    yield db
    db.close()


def run(db: DuckDBManager, name: str, columns: list[str], table: str = "events") -> list[dict]:
    result = tool_registry.get(name).run(db, table, columns)
    assert result.startswith("temp_result_v")
    return db.to_records(result)


def test_bootstrap_ci_supports_statistic_and_iteration_count(advanced_db: DuckDBManager) -> None:
    tool = tool_registry.get("bootstrap_ci")
    result = tool.run(
        advanced_db,
        "events",
        ["amount", "median"],
        n_bootstraps=25,
        seed=123,
    )
    row = advanced_db.to_records(result)[0]
    assert row["statistic"] == "median"
    assert row["estimate"] == pytest.approx(100.0)
    assert row["ci_low"] <= row["estimate"] <= row["ci_high"]


def test_bootstrap_ci_rejects_oversized_input(advanced_db: DuckDBManager) -> None:
    tool = tool_registry.get("bootstrap_ci")
    with pytest.raises(ValueError, match="at most 2 observations"):
        tool.run(advanced_db, "events", ["amount"], max_observations=2)


def test_bootstrap_corr_returns_deterministic_interval(advanced_db: DuckDBManager) -> None:
    tool = tool_registry.get("bootstrap_corr")
    first = advanced_db.to_records(
        tool.run(advanced_db, "events", ["amount", "amount"], n_bootstraps=25, seed=123)
    )[0]
    second = advanced_db.to_records(
        tool.run(advanced_db, "events", ["amount", "amount"], n_bootstraps=25, seed=123)
    )[0]
    assert first == second
    assert float(first["correlation"]) == pytest.approx(1.0)


def test_funnel_steps_counts_distinct_users(advanced_db: DuckDBManager) -> None:
    rows = run(advanced_db, "funnel_steps", ["event_type", "event_time", "user_id"])
    assert [(row["step"], row["count"]) for row in rows] == [
        ("landing", 3),
        ("signup", 3),
        ("purchase", 1),
    ]
    assert float(rows[-1]["conversion_rate"]) == pytest.approx(1 / 3)


def test_funnel_drop_off_and_conversion_alias(advanced_db: DuckDBManager) -> None:
    rows = run(advanced_db, "funnel-drop-off", ["event_type", "event_time"])
    assert rows[2]["drop_off"] == 2
    conversion = run(
        advanced_db,
        "conversion_funnel",
        ["event_type", "user_id", "event_time"],
    )
    assert conversion[-1]["count"] == 1


def test_cohort_and_retention_tools(advanced_db: DuckDBManager) -> None:
    cohort = run(advanced_db, "cohort_analysis", ["cohort", "period", "amount"], "retention")
    assert float(cohort[0]["metric"]) == pytest.approx(180.0)
    retention = run(advanced_db, "retention_matrix", ["user_id", "period"], "retention")
    assert retention[0]["active_users"] == 2
    assert retention[0]["retention_rate"] == pytest.approx(1.0)


def test_domain_tools_return_expected_metrics(advanced_db: DuckDBManager) -> None:
    revenue = run(advanced_db, "revenue_analysis", ["amount", "event_time", "product_id"])
    assert sum(row["revenue_sum"] for row in revenue) == pytest.approx(790.0)
    products = run(advanced_db, "product_performance", ["amount", "product_id"])
    assert products[0]["segment"] == "p1"
    average = run(advanced_db, "avg_order_value", ["amount", "product_id"])
    p1_average = next(row for row in average if row["segment"] == "p1")
    assert float(p1_average["avg_order_value"]) == pytest.approx(122.0)


def test_trend_and_decomposition_expose_components(advanced_db: DuckDBManager) -> None:
    trend_tool = tool_registry.get("trend_analysis")
    trend = advanced_db.to_records(
        trend_tool.run(
            advanced_db,
            "events",
            ["event_time", "amount"],
            seasonality_period=4,
        )
    )
    assert {"period", "trend", "seasonal_index", "residual"} <= trend[0].keys()
    assert len(trend) == 7
    decomposition_tool = tool_registry.get("seasonal_decompose")
    decomposition = run(
        advanced_db, "seasonal_decompose", ["event_time", "amount", "additive", "4"]
    )
    assert decomposition[0]["mode"] == "additive"
    multiplicative = advanced_db.to_records(
        decomposition_tool.run(
            advanced_db,
            "events",
            ["event_time", "amount", "multiplicative"],
            seasonality_period=4,
        )
    )
    assert float(multiplicative[0]["residual"]) > 0
