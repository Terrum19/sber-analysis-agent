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
    with pytest.raises(ValueError, match="two full seasons"):
        run(advanced_db, "seasonal_decompose", ["event_time", "amount", "additive", "4"])


@pytest.fixture
def series_db() -> DuckDBManager:
    db = DuckDBManager(":memory:")
    db.execute(
        """
        CREATE TABLE series AS
        SELECT DATE '2020-01-01' + INTERVAL (3 * quarter_number) MONTH AS quarter_start,
               (100 + 5 * quarter_number) * (CASE quarter_number % 4 WHEN 0 THEN 1.2
                                                  WHEN 1 THEN 0.9 WHEN 2 THEN 0.8 ELSE 1.1 END)
                   AS amount,
               100 + 5 * quarter_number
                   + (CASE quarter_number % 4 WHEN 0 THEN 12 WHEN 1 THEN -6
                                            WHEN 2 THEN -9 ELSE 3 END) AS additive_amount
        FROM range(16) AS quarters(quarter_number)
        """
    )
    yield db
    db.close()


def test_trend_is_fitted_against_time(series_db: DuckDBManager) -> None:
    series_db.execute(
        "CREATE TABLE gaps AS SELECT * FROM (VALUES (DATE '2024-01-01', 0.0), "
        "(DATE '2024-01-02', 1.0), (DATE '2024-01-11', 10.0)) AS rows(day, value)"
    )
    rows = run(series_db, "trend_analysis", ["day", "value"], "gaps")
    assert float(rows[0]["slope"]) == pytest.approx(1.0)
    assert float(rows[0]["r_squared"]) == pytest.approx(1.0)


def test_additive_decomposition_recovers_the_seasonal_pattern(series_db: DuckDBManager) -> None:
    rows = series_db.to_records(
        tool_registry.get("seasonal_decompose").run(
            series_db,
            "series",
            ["quarter_start", "additive_amount", "additive"],
            seasonality_period=4,
        )
    )
    assert rows[0]["trend"] is None
    assert float(rows[2]["trend"]) == pytest.approx(110.0)
    seasonal = [float(row["seasonal"]) for row in rows[:4]]
    assert seasonal == pytest.approx([12.0, -6.0, -9.0, 3.0])
    assert all(abs(float(row["residual"])) < 1e-9 for row in rows if row["residual"] is not None)


def test_multiplicative_decomposition_keeps_a_positive_trend(series_db: DuckDBManager) -> None:
    rows = series_db.to_records(
        tool_registry.get("seasonal_decompose").run(
            series_db,
            "series",
            ["quarter_start", "amount", "multiplicative"],
            seasonality_period=4,
        )
    )
    trends = [float(row["trend"]) for row in rows if row["trend"] is not None]
    assert trends and all(value > 0 for value in trends)
    assert sum(float(row["seasonal"]) for row in rows[:4]) == pytest.approx(4.0)
    assert all(float(row["residual"]) > 0 for row in rows if row["residual"] is not None)


def test_funnel_ignores_stray_early_events_and_requires_previous_steps(
    advanced_db: DuckDBManager,
) -> None:
    advanced_db.execute(
        """
        CREATE TABLE journeys AS SELECT * FROM (
            VALUES
                ('u1', 'visit', 1), ('u1', 'cart', 2), ('u1', 'buy', 3),
                ('u2', 'buy', 0),
                ('u3', 'visit', 5), ('u3', 'visit', 6), ('u3', 'visit', 7),
                ('u4', 'cart', 8)
        ) AS rows(user_id, step, ts)
        """
    )
    steps = run(advanced_db, "funnel_steps", ["step", "ts", "user_id"], "journeys")
    assert [(row["step"], row["count"]) for row in steps] == [
        ("visit", 2),
        ("cart", 1),
        ("buy", 1),
    ]
    drop_off = run(advanced_db, "funnel_drop_off", ["step", "ts", "user_id"], "journeys")
    assert [row["drop_off"] for row in drop_off] == [0, 1, 0]
    assert all(float(row["drop_off_rate"]) >= 0 for row in drop_off)


def test_funnel_follows_the_planned_step_order(advanced_db: DuckDBManager) -> None:
    result = tool_registry.get("conversion_funnel").run(
        advanced_db,
        "events",
        ["event_type", "user_id", "event_time"],
        steps=["signup", "purchase"],
    )
    rows = advanced_db.to_records(result)
    assert [(row["step"], row["count"]) for row in rows] == [("signup", 3), ("purchase", 1)]
