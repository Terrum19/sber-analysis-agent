"""Unit tests for research tools and their DuckDB contracts."""

import asyncio
import time

import pytest

from app.core.data.duckdb_manager import DuckDBManager
from app.core.tools.base import BaseTool, ToolContext
from app.core.tools.registry import tool_registry


@pytest.fixture
def db() -> DuckDBManager:
    manager = DuckDBManager(":memory:")
    manager.execute(
        """
        CREATE TABLE raw AS SELECT * FROM (
            VALUES
                (1, DATE '2024-01-01', 'a', 10.0, 2.0),
                (2, DATE '2024-02-01', 'b', 20.0, 4.0),
                (3, DATE '2024-03-01', 'a', 30.0, 8.0),
                (4, DATE '2024-04-01', 'b', 40.0, 10.0)
        ) AS rows(id, event_date, category, amount, other_amount)
        """
    )
    manager.execute(
        """
        CREATE TABLE right_table AS SELECT * FROM (
            VALUES (1, 'first'), (3, 'third')
        ) AS rows(id, label)
        """
    )
    yield manager
    manager.close()


def run_tool(db: DuckDBManager, name: str, columns: list[str]) -> tuple[str, list[dict]]:
    table = tool_registry.get(name).run(db, "raw", columns)
    assert table.startswith("temp_result_v")
    return table, db.to_records(table)


@pytest.mark.parametrize(
    ("name", "columns"),
    [
        ("select_columns", ["id", "amount"]),
        ("filter_rows", ["amount", "20", ">="]),
        ("create_column", ["total", "amount + other_amount"]),
        ("rename_column", ["amount", "value"]),
        ("join_tables", ["right_table", "t.id = t_right.id"]),
        ("left_join_tables", ["right_table", "t.id = t_right.id"]),
        ("group_by", ["category", "amount", "SUM"]),
        ("pivot", ["amount", "category", "id"]),
        ("time_bucket", ["event_date", "month"]),
        ("lag", ["amount", "1", "event_date"]),
        ("lead", ["amount", "1", "event_date"]),
        ("aggregate", ["category"]),
        ("time_series", ["event_date", "amount"]),
        ("correlation", ["amount", "other_amount"]),
    ],
)
def test_sql_tools_return_cached_temp_tables(
    db: DuckDBManager, name: str, columns: list[str]
) -> None:
    table, records = run_tool(db, name, columns)
    assert db.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"] == len(records)


def test_atomic_tools_preserve_expected_values(db: DuckDBManager) -> None:
    _, records = run_tool(db, "create_column", ["total", "amount + other_amount"])
    assert records[0]["total"] == 12

    _, records = run_tool(db, "filter_rows", ["category", "a", "="])
    assert [row["id"] for row in records] == [1, 3]

    _, records = run_tool(db, "left_join_tables", ["right_table", "t.id = t_right.id"])
    assert next(row for row in records if row["id"] == 2)["label"] is None


@pytest.mark.parametrize(
    ("name", "columns", "expected_columns"),
    [
        ("pearson_corr", ["amount", "other_amount"], {"correlation", "p_value", "n"}),
        ("spearman_corr", ["amount", "other_amount"], {"correlation", "p_value", "n"}),
        (
            "linear_regression",
            ["amount", "other_amount"],
            {"slope", "intercept", "r_squared", "p_value", "stderr", "n"},
        ),
        (
            "anova_one_way",
            ["amount", "category"],
            {"statistic", "p_value", "df_between", "df_within", "groups"},
        ),
        (
            "t_test_ind",
            ["amount", "other_amount"],
            {"statistic", "p_value", "degrees_of_freedom"},
        ),
        (
            "t_test_dep",
            ["amount", "other_amount"],
            {"statistic", "p_value", "degrees_of_freedom", "n"},
        ),
        ("mann_whitney_u", ["amount", "other_amount"], {"statistic", "p_value"}),
        ("ks_test", ["amount"], {"statistic", "p_value"}),
        ("shapiro_wilk", ["amount"], {"statistic", "p_value"}),
        (
            "describe_stats",
            ["amount"],
            {"n", "mean", "median", "std", "min", "max", "q1", "q3", "skew", "kurtosis"},
        ),
        ("bootstrap_ci", ["amount"], {"statistic", "estimate", "ci_low", "ci_high"}),
        ("search_categorical_values", ["category", "a"], {"stored_value", "match_score"}),
    ],
)
def test_statistical_tools_return_results(
    db: DuckDBManager, name: str, columns: list[str], expected_columns: set[str]
) -> None:
    table, records = run_tool(db, name, columns)
    assert records
    assert expected_columns == {row["column_name"] for row in db.query(f"DESCRIBE {table}")}


def test_time_series_and_seasonality_are_aggregations(db: DuckDBManager) -> None:
    _, time_series = run_tool(db, "time_series", ["event_date", "amount"])
    assert [row["metric_avg"] for row in time_series] == [10.0, 20.0, 30.0, 40.0]

    _, seasonality = run_tool(db, "seasonality", ["event_date", "amount"])
    assert seasonality[0]["month"] == 1
    assert seasonality[0]["level"] == 10.0
    assert seasonality[-1]["incomplete"] == 0


@pytest.fixture
def sales() -> DuckDBManager:
    """Два года продаж по две строки в день: декабрь втрое выше, второй год вдвое выше первого."""
    manager = DuckDBManager(":memory:")
    manager.execute(
        """
        CREATE TABLE sales AS
        SELECT day::TIMESTAMP + INTERVAL 10 HOUR AS sold_at, 'north' AS region,
               (CASE WHEN month(day) = 12 THEN 3 ELSE 1 END) * (year(day) - 2022) AS amount
        FROM range(DATE '2023-01-01', DATE '2025-01-01', INTERVAL 1 DAY) AS days(day),
             range(2) AS copies(copy)
        """
    )
    yield manager
    manager.close()


def run_sales(db: DuckDBManager, name: str, table: str = "sales", **arguments) -> list[dict]:
    return db.to_records(tool_registry.get(name).run(db, table, [], **arguments), limit=1000)


def test_seasonality_sums_each_month_then_averages_years(sales: DuckDBManager) -> None:
    rows = run_sales(sales, "seasonality", date_column="sold_at", metric_column="amount")
    assert len(rows) == 12
    assert rows[0] == {
        "month": 1,
        "level": 93.0,
        "median": 93.0,
        "periods": 2,
        "incomplete": 0,
        "skipped": 0,
    }
    assert rows[11]["level"] == 279.0


def test_seasonality_counts_rows_and_keeps_average_for_per_record_metrics(
    sales: DuckDBManager,
) -> None:
    counted = run_sales(sales, "seasonality", date_column="sold_at", function="COUNT")
    assert counted[0]["level"] == 62.0
    averaged = run_sales(
        sales, "seasonality", date_column="sold_at", metric_column="amount", function="avg"
    )
    assert averaged[11]["level"] == 4.5
    with pytest.raises(ValueError, match="requires metric_column"):
        run_sales(sales, "seasonality", date_column="sold_at")


def test_seasonality_skips_partial_and_extra_periods(sales: DuckDBManager) -> None:
    sales.execute(
        "CREATE TABLE uneven AS SELECT * FROM sales "
        "WHERE sold_at >= TIMESTAMP '2023-06-01' AND sold_at < TIMESTAMP '2024-12-15'"
    )
    rows = run_sales(sales, "seasonality", "uneven", date_column="sold_at", metric_column="amount")
    by_month = {row["month"]: row for row in rows}
    assert by_month[12]["incomplete"] == 1
    assert by_month[12]["periods"] == 1 and by_month[12]["skipped"] == 0
    assert by_month[6]["skipped"] == 1 and by_month[6]["periods"] == 1
    assert by_month[6]["level"] == 120.0


@pytest.mark.parametrize("name", ["trend_analysis", "seasonal_decompose"])
def test_series_tools_reject_row_level_data(sales: DuckDBManager, name: str) -> None:
    with pytest.raises(ValueError, match='interval="month", function="SUM"'):
        run_sales(sales, name, date_column="sold_at", value_column="amount")


def test_trend_reports_growth_year_over_year_and_its_significance(sales: DuckDBManager) -> None:
    rows = run_sales(
        sales, "trend_analysis", date_column="sold_at", value_column="amount", interval="month"
    )
    assert len(rows) == 24
    assert rows[0]["yoy_change"] is None
    assert rows[12]["yoy_change"] == pytest.approx(1.0)
    assert rows[0]["yoy_change_avg"] == pytest.approx(1.0, abs=0.01)
    assert rows[0]["slope_per_period"] > 0
    assert rows[0]["p_value"] < 0.05


def test_trend_compares_with_the_same_calendar_month(sales: DuckDBManager) -> None:
    sales.execute(
        "CREATE TABLE holes AS SELECT * FROM sales WHERE month(sold_at) != 3 OR year(sold_at) != 2023"
    )
    rows = run_sales(
        sales,
        "trend_analysis",
        "holes",
        date_column="sold_at",
        value_column="amount",
        interval="month",
    )
    april = next(row for row in rows if str(row["period"]).startswith("2024-04"))
    assert april["yoy_change"] == pytest.approx(1.0)


def test_trend_by_region_keeps_one_series_per_region(sales: DuckDBManager) -> None:
    sales.execute(
        "CREATE TABLE regions AS SELECT * FROM sales "
        "UNION ALL SELECT sold_at, 'south' AS region, amount * 2 AS amount FROM sales"
    )
    bucketed = tool_registry.get("time_bucket").run(
        sales,
        "regions",
        [],
        date_column="sold_at",
        interval="month",
        metric_column="amount",
        function="SUM",
        by=["region"],
    )
    with pytest.raises(ValueError, match=r"by"):
        run_sales(sales, "trend_analysis", bucketed, date_column="period", value_column="amount")
    rows = run_sales(
        sales,
        "trend_analysis",
        bucketed,
        date_column="period",
        value_column="amount",
        by=["region"],
    )
    assert {row["region"] for row in rows} == {"north", "south"}
    assert all(row["yoy_change_avg"] == pytest.approx(1.0, abs=0.01) for row in rows)


def test_series_tools_aggregate_by_hour_with_a_daily_season(sales: DuckDBManager) -> None:
    rows = run_sales(
        sales,
        "trend_analysis",
        date_column="sold_at",
        value_column="amount",
        interval="hour",
        function="COUNT",
    )
    assert len(rows) == 731
    assert all(row["value"] == 2 for row in rows)
    with pytest.raises(ValueError, match="без пропусков"):
        run_sales(
            sales,
            "seasonal_decompose",
            date_column="sold_at",
            value_column="amount",
            interval="hour",
            function="COUNT",
        )


@pytest.fixture
def months() -> DuckDBManager:
    """Три года по месяцам, значение растёт на 1 каждый месяц."""
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE months AS SELECT (DATE '2023-01-01' + INTERVAL (i) MONTH)::DATE AS month, "
        "(i + 1)::DOUBLE AS amount FROM range(36) AS steps(i)"
    )
    yield manager
    manager.close()


def test_trend_keeps_growth_out_of_the_seasonal_effects(months: DuckDBManager) -> None:
    months.execute("CREATE TABLE two_years AS SELECT * FROM months WHERE month < DATE '2025-01-01'")
    for table in ("months", "two_years"):
        rows = run_sales(
            months, "trend_analysis", table, date_column="month", value_column="amount"
        )
        assert rows[0]["slope_per_period"] == pytest.approx(1.0)
        assert rows[0]["p_value"] < 0.001
        assert all(row["seasonal_index"] == pytest.approx(1.0) for row in rows)


def test_decompose_leaves_a_missing_month_as_a_gap(months: DuckDBManager) -> None:
    months.execute("CREATE TABLE holes AS SELECT * FROM months WHERE month != DATE '2023-03-01'")
    rows = run_sales(
        months, "seasonal_decompose", "holes", date_column="month", value_column="amount"
    )
    trend = {str(row["period"])[:7]: row["trend"] for row in rows}
    assert trend["2023-08"] is None
    assert trend["2024-06"] == pytest.approx(18.0)


def test_seasonality_skips_a_month_without_data() -> None:
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE days AS SELECT day::DATE AS day, 10.0 AS amount "
        "FROM range(DATE '2021-01-01', DATE '2024-01-01', INTERVAL 1 DAY) AS days(day) "
        "WHERE year(day) != 2022 OR month(day) != 7"
    )
    rows = run_sales(manager, "seasonality", "days", date_column="day", metric_column="amount")
    by_month = {row["month"]: row for row in rows}
    assert (by_month[6]["level"], by_month[6]["periods"]) == (300.0, 3)
    assert (by_month[7]["level"], by_month[7]["periods"]) == (310.0, 2)
    manager.close()


@pytest.fixture
def nights() -> DuckDBManager:
    """Четыре недели: продажи по 10 в 12:00 каждый день, ночью (3:00) — только по понедельникам."""
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE nights AS "
        "SELECT day::TIMESTAMP + INTERVAL 12 HOUR AS sold_at, 10.0::DOUBLE AS amount "
        "FROM range(DATE '2024-01-01', DATE '2024-01-29', INTERVAL 1 DAY) AS days(day) "
        "UNION ALL SELECT day::TIMESTAMP + INTERVAL 3 HOUR, 7.0 "
        "FROM range(DATE '2024-01-01', DATE '2024-01-29', INTERVAL 7 DAY) AS days(day)"
    )
    yield manager
    manager.close()


def test_seasonality_counts_an_empty_hour_as_zero(nights: DuckDBManager) -> None:
    # Час без продаж — 0 ₽: иначе ночь — среднее только по понедельникам, 7 вместо 0,7.
    # 01.01 3:00 — сверх целых суток (skipped), остаются 27 ночей с 02.01 по 28.01.
    rows = run_sales(
        nights,
        "seasonality",
        "nights",
        date_column="sold_at",
        metric_column="amount",
        period="hour",
    )
    by_hour = {row["hour"]: row for row in rows}
    assert by_hour[3]["periods"] == 27
    assert by_hour[3]["level"] == pytest.approx(21.0 / 27)
    assert by_hour[3]["median"] == 0.0
    assert by_hour[12]["level"] == 10.0


def test_seasonality_median_exposes_an_outlier_day(nights: DuckDBManager) -> None:
    # Среды 03.01, 10.01, 17.01, 24.01: четыре недели охвата — четыре целых недели.
    nights.execute("INSERT INTO nights VALUES (TIMESTAMP '2024-01-10 12:00', 1000.0)")
    rows = run_sales(
        nights,
        "seasonality",
        "nights",
        date_column="sold_at",
        metric_column="amount",
        period="dayofweek",
    )
    wednesday = rows[2]
    assert wednesday["dayofweek"] == 3
    assert wednesday["level"] == 260.0
    assert wednesday["median"] == 10.0


def test_seasonality_by_week_of_year_averages_years(sales: DuckDBManager) -> None:
    # Первая и последняя недели неполные, но два года охвата — два целых года, а не один.
    rows = run_sales(
        sales, "seasonality", date_column="sold_at", metric_column="amount", period="week"
    )
    by_week = {row["week"]: row for row in rows}
    assert sum(row["skipped"] for row in rows) == 0
    assert (by_week[10]["level"], by_week[10]["periods"]) == (21.0, 2)
    # Даты — последнего учтённого года: 10-я неделя 2024 года — 04.03–10.03.
    assert by_week[10]["dates"] == "04.03–10.03"


def test_seasonality_average_keeps_empty_periods_out(nights: DuckDBManager) -> None:
    rows = run_sales(
        nights,
        "seasonality",
        "nights",
        date_column="sold_at",
        metric_column="amount",
        period="hour",
        function="AVG",
    )
    by_hour = {row["hour"]: row for row in rows}
    assert (by_hour[3]["level"], by_hour[3]["periods"]) == (7.0, 3)


def test_trend_reads_text_days_as_days_not_months() -> None:
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE days AS SELECT strftime(DATE '2024-01-01' + INTERVAL (i) DAY, '%Y-%m-%d') "
        "AS day, i::DOUBLE AS amount FROM range(30) AS steps(i)"
    )
    rows = run_sales(manager, "trend_analysis", "days", date_column="day", value_column="amount")
    assert rows[0]["slope_per_period"] == pytest.approx(1.0)
    manager.close()


@pytest.mark.parametrize(
    ("interval", "end", "step", "last_incomplete"),
    [
        ("day", "2024-02-01", "1 DAY", 0),
        ("hour", "2024-01-03", "1 HOUR", 0),
        ("month", "2024-03-15 12:00", "7 HOUR", 1),
    ],
)
def test_midnight_timestamps_cover_the_whole_last_period(
    interval: str, end: str, step: str, last_incomplete: int
) -> None:
    manager = DuckDBManager(":memory:")
    manager.execute(
        f"CREATE TABLE events AS SELECT moment AS moment, 1.0 AS amount FROM range("
        f"TIMESTAMP '2024-01-01', TIMESTAMP '{end}', INTERVAL {step}) AS moments(moment)"
    )
    rows = run_sales(
        manager,
        "trend_analysis",
        "events",
        date_column="moment",
        value_column="amount",
        interval=interval,
    )
    assert rows[0]["incomplete"] == 0
    assert rows[-1]["incomplete"] == last_incomplete
    manager.close()


def test_forecast_returns_requested_number_of_periods(db: DuckDBManager) -> None:
    table, records = run_tool(db, "forecast", ["event_date", "amount", "2"])
    assert len(records) == 2
    assert set(db.query(f"DESCRIBE {table}")[0]) >= {"column_name"}


def test_forecast_extends_the_fitted_line(db: DuckDBManager) -> None:
    _, records = run_tool(db, "forecast", ["event_date", "amount", "2"])
    assert [
        (row["period_index"], row["value"]) for row in records if row["kind"] == "forecast"
    ] == [
        (4, pytest.approx(50.0)),
        (5, pytest.approx(60.0)),
    ]


def test_rename_column_keeps_column_position(db: DuckDBManager) -> None:
    table, _ = run_tool(db, "rename_column", ["category", "segment"])
    names = [row["column_name"] for row in db.query(f"DESCRIBE {table}")]
    assert names == ["id", "event_date", "segment", "amount", "other_amount"]


def test_window_tools_take_the_order_column_from_parameters(db: DuckDBManager) -> None:
    db.execute("CREATE VIEW raw_view AS SELECT * FROM raw")
    table = tool_registry.get("lag").run(db, "raw_view", ["amount", "1"], order_column="event_date")
    assert [row["lag_amount"] for row in db.to_records(table)] == [None, 10.0, 20.0, 30.0]


def test_expressions_may_use_scalar_functions_and_join_aliases(db: DuckDBManager) -> None:
    _, records = run_tool(
        db, "create_column", ["label", "CASE WHEN amount > 15 THEN upper(category) ELSE 'low' END"]
    )
    assert [row["label"] for row in records] == ["low", "B", "A", "B"]
    _, records = run_tool(db, "join_tables", ["right_table", "t1.id = t2.id AND t2.label <> ''"])
    assert [row["label"] for row in records] == ["first", "third"]


@pytest.mark.parametrize(
    ("name", "columns"),
    [
        ("select_columns", []),
        ("filter_rows", ["amount"]),
        ("create_column", ["new"]),
        ("group_by", ["category", "amount", "MODE"]),
        ("time_bucket", ["event_date", "decade"]),
        ("lag", ["amount", "-1", "event_date"]),
        ("lag", ["amount", "1"]),
        ("create_column", ["leak", "(SELECT content FROM read_text('/etc/hostname'))"]),
        ("create_column", ["leak", "EXISTS (SELECT 1)"]),
        ("create_column", ["leak", "amount) AS a, (amount"]),
        ("create_column", ["leak", "1) AS a FROM read_text('/etc/hostname') /*"]),
        ("create_column", ["leak", "current_setting('home_directory')"]),
        ("join_tables", ["right_table", "t.id = t_right.id AND (SELECT 1) = 1"]),
        ("pearson_corr", ["amount"]),
        ("bootstrap_ci", ["amount", "mode"]),
        ("search_categorical_values", ["category"]),
    ],
)
def test_tools_reject_invalid_arguments(db: DuckDBManager, name: str, columns: list[str]) -> None:
    with pytest.raises(ValueError):
        tool_registry.get(name).run(db, "raw", columns)


def test_registry_is_extensible_without_orchestrator_changes(db: DuckDBManager) -> None:
    class MarkerTool(BaseTool):
        name = "marker"
        description = "marker"

        def execute(self, db, input_table, args):
            return db.save_step_result(input_table, "SELECT 1 AS marker")

        def describe(self):
            return f"{self.name}: {self.description}"

    tool_registry.register(MarkerTool())
    try:
        assert tool_registry.get("marker").run(db, "raw", []) == "temp_result_v1"
        assert "marker: marker" in tool_registry.describe()
    finally:
        tool_registry._tools.pop("marker", None)


async def test_arun_keeps_loop_free_while_sql_runs(db: DuckDBManager) -> None:
    # #189: SQL тула в loop хода задерживал события шагов и пинги SSE.
    class SlowTool(BaseTool):
        name = "slow"
        description = "slow"

        def execute(self, db, input_table, args):
            time.sleep(0.3)
            return db.save_step_result(input_table, "SELECT 1 AS slow")

    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    ticker = asyncio.create_task(tick())
    try:
        assert await SlowTool().arun(ToolContext(), db, "raw") == "temp_result_v1"
    finally:
        ticker.cancel()
    assert ticks >= 10


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("time_bucket", {"date_column": "event_date", "function": "SUM"}),
        ("time_series", {"date_column": "event_date"}),
        ("seasonality", {"date_column": "event_date"}),
        ("forecast", {"date_column": "event_date"}),
        ("cohort_analysis", {"cohort_column": "category", "period_column": "event_date"}),
        ("product_performance", {"group_column": "category"}),
    ],
)
def test_column_is_alias_of_metric_column(name: str, arguments: dict) -> None:
    # #150: модель пишет column, как у group_by/aggregate.
    args = tool_registry.get(name).parse_args(arguments={**arguments, "column": "amount"})
    assert args.metric_column == "amount"


def test_time_bucket_explicit_metric_column_is_unchanged() -> None:
    tool = tool_registry.get("time_bucket")
    args = tool.parse_args(arguments={"date_column": "event_date", "metric_column": "amount"})
    assert args.metric_column == "amount"
    # Оба сразу — неоднозначно: не угадываем, план переспрашивается.
    with pytest.raises(ValueError, match="Extra inputs"):
        tool.parse_args(
            arguments={"date_column": "event_date", "metric_column": "amount", "column": "id"}
        )


def test_time_bucket_with_column_runs(db: DuckDBManager) -> None:
    table = tool_registry.get("time_bucket").run(
        db, "raw", date_column="event_date", function="SUM", column="amount"
    )
    assert [row["sum_amount"] for row in db.to_records(table)] == [10.0, 20.0, 30.0, 40.0]


def test_validate_plan_accepts_time_bucket_with_column() -> None:
    from app.core.agent.planner import validate_plan
    from app.models.research_step import ResearchPlan, ResearchStep

    step = ResearchStep(
        step_id=0,
        tool="time_bucket",
        arguments={"date_column": "event_date", "function": "SUM", "column": "amount"},
    )
    assert validate_plan(ResearchPlan(steps=[step])) == []


def test_filter_by_date_keeps_the_whole_day_of_a_timestamp(db: DuckDBManager) -> None:
    """«По 31.12» на TIMESTAMP — включая 31 декабря после полуночи."""
    from app.core.tools.sql.atomic import FilterRowsArgs, FilterRowsTool

    db.execute(
        """
        CREATE TABLE ts AS SELECT * FROM (
            VALUES (1, TIMESTAMP '2025-01-01 00:00:00'), (2, TIMESTAMP '2025-03-15 10:30:00'),
                   (3, TIMESTAMP '2025-12-31 18:00:00'), (4, TIMESTAMP '2026-01-01 00:00:00')
        ) AS rows(id, happened)
        """
    )
    tool = FilterRowsTool()

    def ids(operator: str, value: object, table: str = "ts") -> list[int]:
        args = FilterRowsArgs(column="happened", operator=operator, value=value)
        out = tool.execute(db, table, args)
        return sorted(row["id"] for row in db.to_records(out))

    assert ids("BETWEEN", ["2025-01-01", "2025-12-31"]) == [1, 2, 3]
    assert ids("<=", "2025-12-31") == [1, 2, 3]
    assert ids(">", "2025-12-31") == [4]
    assert ids("=", "2025-03-15") == [2]
    # Время указано или колонка — DATE: сравнение как есть
    assert ids("<=", "2025-12-31 12:00:00") == [1, 2]
    assert [
        row["id"]
        for row in db.to_records(
            tool.execute(
                db,
                "raw",
                FilterRowsArgs(
                    column="event_date", operator="BETWEEN", value=["2024-01-01", "2024-02-01"]
                ),
            )
        )
    ] == [1, 2]
