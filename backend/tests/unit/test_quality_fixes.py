"""Исправления качества за флагом agent_quality_fixes."""

import pytest

from app.config import settings
from app.core.agent.quality_fixes import insight_facts
from app.core.data.duckdb_manager import DuckDBManager
from app.core.tools.registry import tool_registry
from app.models.research_step import ResearchStep


@pytest.fixture
def db(tmp_path) -> DuckDBManager:
    manager = DuckDBManager(str(tmp_path / "q.duckdb"))
    manager.query("CREATE TABLE t AS SELECT range AS id, range * 10 AS amount FROM range(120)")
    return manager


def test_group_by_without_keys_gives_total(db) -> None:
    result = tool_registry.get("group_by").run(db, "t", [], by=[], column="amount", function="SUM")
    assert db.to_records(result) == [{"sum_amount": sum(range(120)) * 10}]


def test_insight_facts_name_the_season_and_show_shared_values(db) -> None:
    db.execute(
        "CREATE TABLE season AS SELECT * FROM (VALUES (1, 10.0, 0.0002), (12, 30.0, 0.0002))"
        " AS rows(month, level, p_value)"
    )
    facts = insight_facts(db, "season", shown=50)
    assert "level: сумма 40.0, среднее 20.0, мин 10.0 (month = 1), макс 30.0 (month = 12)" in facts
    assert "- p_value: 0.0002 (одно значение во всех строках)" in facts
    assert "- month:" not in facts


def test_insight_facts_count_whole_table(db) -> None:
    facts = insight_facts(db, "t", shown=50)
    assert "строк в результате: 120 (показано модели: 50)" in facts
    assert "amount: сумма 71400" in facts


def test_insight_facts_skip_non_numeric_types(db) -> None:
    # INTERVAL содержит «INT», но SUM по нему DuckDB не считает.
    db.query("CREATE TABLE w AS SELECT INTERVAL 3 DAY AS d, 'a' AS s, 1.5::DECIMAL(4,1) AS x")
    facts = insight_facts(db, "w", shown=50)
    assert "- d:" not in facts and "- s:" not in facts
    assert "x: сумма 1.5" in facts


def test_tool_hints_only_with_flag(monkeypatch) -> None:
    monkeypatch.setattr(settings, "agent_quality_fixes", False)
    assert "НЕ сумма" not in tool_registry.describe()
    monkeypatch.setattr(settings, "agent_quality_fixes", True)
    assert "НЕ сумма" in tool_registry.describe()


def test_ungrounded_numbers_detects_miscopied_count() -> None:
    from app.core.agent.quality_fixes import ungrounded_numbers

    prompt = "Вопрос: больше 150?\n- строк в результате: 471 (показано модели: 50)"
    assert ungrounded_numbers("Событий больше 150 — 468", prompt) == ["468"]
    assert ungrounded_numbers("Событий больше 150 — 471", prompt) == []
    assert ungrounded_numbers("Сумма — 271 050", "сумма 271050") == []


def test_ungrounded_numbers_allows_rounding_and_shares() -> None:
    from app.core.agent.quality_fixes import ungrounded_numbers

    assert ungrounded_numbers("Среднее — 12.3", "среднее 12.34") == []
    assert ungrounded_numbers("Среднее — 12", "среднее 12.34") == []
    assert ungrounded_numbers("Среднее — 12.4", "среднее 12.34") == ["12.4"]
    assert ungrounded_numbers("Москва — 40% событий", "[{'n': 4}, {'n': 6}]") == []


def test_ungrounded_numbers_reads_thousands_and_millions() -> None:
    from app.core.agent.quality_fixes import ungrounded_numbers

    assert ungrounded_numbers("Выручка 726,9 млн руб.", "726930548.86") == []
    assert ungrounded_numbers("Наклон ≈ 22,6 тыс. ₽/мес.", "slope 22643.75") == []
    assert ungrounded_numbers("Выручка 0,73 млрд", "726930548.86") == []
    assert ungrounded_numbers("Выручка 726,9 млн", "выручка_млн 726.93") == []
    assert ungrounded_numbers("Выручка 726,9", "726930548.86") == ["726,9"]
    assert ungrounded_numbers("Выручка 730 млн", "726930548.86") == ["730"]


def test_time_bucket_avg_without_metric_fails_validation() -> None:
    tool = tool_registry.get("time_bucket")
    with pytest.raises(ValueError):
        tool.parse_args([], {"date_column": "d", "function": "AVG"})


def test_select_tools_keeps_core_and_matches_question() -> None:
    from app.core.agent.quality_fixes import select_tools

    text = select_tools("Есть ли тренд выручки?")
    assert "- group_by" in text and "- trend_analysis" in text
    assert "- funnel_steps" not in text


@pytest.mark.parametrize(
    ("question", "tool"),
    [
        ("Что происходит с выручкой каждый июль?", "period_compare"),
        ("Есть ли аномальные значения возраста респондентов?", "detect_anomalies"),
        ("Есть ли выбросы в ценах?", "detect_anomalies"),
        ("Есть ли запаздывание между рекламой и выручкой?", "lag_correlation"),
        ("Растёт ли выручка?", "trend_analysis"),
        ("растет ли выручка в зависимости от месяца", "trend_analysis"),
        ("Есть ли сезонность в продажах?", "seasonality"),
    ],
)
def test_select_tools_offers_scenario_three_tools(question: str, tool: str) -> None:
    from app.core.agent.quality_fixes import select_tools

    assert f"- {tool} [" in select_tools(question)


class _ScriptedLLM:
    """complete_structured отдаёт заготовленные заголовки по очереди."""

    def __init__(self, *headlines: str) -> None:
        self.headlines = list(headlines)
        self.prompts: list[str] = []

    async def complete_structured(self, system, user, schema):
        self.prompts.append(user)
        return schema(headline=self.headlines.pop(0))


async def test_insight_asks_again_for_ungrounded_number(db, monkeypatch) -> None:
    from app.core.agent.insight import InsightGenerator

    monkeypatch.setattr(settings, "agent_quality_fixes", True)
    llm = _ScriptedLLM("Строк — 118", "Строк — 120")
    insight = await InsightGenerator(llm=llm, db=db).generate("Сколько строк?", "t")
    assert insight.headline == "Строк — 120"
    assert len(llm.prompts) == 2 and "118" in llm.prompts[1]


async def test_insight_without_facts_when_they_fail(db, monkeypatch) -> None:
    from app.core.agent import insight as module

    def broken(*args, **kwargs):
        raise RuntimeError("binder error")

    monkeypatch.setattr(settings, "agent_quality_fixes", True)
    monkeypatch.setattr(module, "insight_facts", broken)
    llm = _ScriptedLLM("Данных достаточно")
    insight = await module.InsightGenerator(llm=llm, db=db).generate("Сколько строк?", "t")
    assert insight.headline == "Данных достаточно"
    assert len(llm.prompts) == 1


def test_insight_facts_name_the_rows_of_min_and_max(db) -> None:
    db.query(
        "CREATE TABLE m AS SELECT * FROM (VALUES "
        "(TIMESTAMP '2024-01-01', 26484), (TIMESTAMP '2024-08-01', 19362), "
        "(TIMESTAMP '2024-10-01', 21833)) AS v(period, sum_amount)"
    )
    facts = insight_facts(db, "m", shown=50)
    assert "мин 19362 (period = 2024-08-01)" in facts
    assert "макс 26484 (period = 2024-01-01)" in facts


def test_single_row_facts_have_no_labels(db) -> None:
    db.query("CREATE TABLE one AS SELECT 'all' AS k, 5 AS n")
    assert "(k =" not in insight_facts(db, "one", shown=50)


def test_rows_are_shown_as_compact_table() -> None:
    from datetime import date, datetime

    from app.core.agent.quality_fixes import format_rows

    rows = [
        {"period": datetime(2024, 1, 1), "avg": 542.1666, "day": date(2024, 3, 5)},
        {"period": datetime(2024, 2, 1, 12, 30), "avg": 7, "day": date(2024, 3, 6)},
    ]
    assert format_rows(rows) == (
        "period | avg | day\n2024-01-01 | 542.17 | 2024-03-05\n2024-02-01 12:30:00 | 7 | 2024-03-06"
    )
    assert format_rows([]) == "пусто"


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (3366 / 129971, "0.0259"),
        (0.00413, "0.00413"),
        (542.1666, "542.17"),
        (1e-30, "1e-30"),
        (7, "7"),
    ],
)
def test_small_fractions_keep_three_significant_digits(value: object, text: str) -> None:
    from app.core.agent.quality_fixes import format_value

    assert format_value(value) == text


async def test_insight_prompt_keeps_small_share(db, monkeypatch) -> None:
    from app.core.agent.insight import InsightGenerator

    monkeypatch.setattr(settings, "agent_quality_fixes", True)
    db.query("CREATE TABLE share AS SELECT 3366 / 129971 AS share")
    llm = _ScriptedLLM("Доля — 0.0259")
    await InsightGenerator(llm=llm, db=db).generate("Какая доля вин дороже $100?", "share")
    assert "share\n0.0259" in llm.prompts[0]


def _step(step_id: int, tool: str, **arguments) -> ResearchStep:
    return ResearchStep(step_id=step_id, tool=tool, arguments=arguments)


SHARE_PLAN = [
    _step(
        1,
        "create_column",
        new_column="is_expensive",
        expression="CASE WHEN price > 100 THEN 1 ELSE 0 END",
    ),
    _step(
        2,
        "group_by",
        by=[],
        metrics=[
            {"function": "SUM", "column": "is_expensive", "alias": "expensive"},
            {"function": "COUNT", "alias": "total"},
        ],
    ),
    _step(3, "create_column", new_column="share", expression="expensive * 1.0 / total"),
]
VALUE_PLAN = [
    _step(1, "filter_rows", column="price", operator=">", value=0),
    _step(2, "create_column", new_column="value_ratio", expression="points * 1.0 / price"),
    _step(3, "sort_limit", order_by="value_ratio", descending=True, limit=10),
]
WINE_COLUMNS = ["country", "description", "points", "price", "price_eur", "region_2"]


def test_plan_columns_finds_source_columns_of_plan() -> None:
    from app.core.agent.quality_fixes import plan_columns

    assert plan_columns(SHARE_PLAN, WINE_COLUMNS) == ["price"]
    assert plan_columns(VALUE_PLAN, WINE_COLUMNS) == ["points", "price"]
    eur = [_step(1, "create_column", new_column="x", expression="price_eur * 2")]
    assert plan_columns(eur, WINE_COLUMNS) == ["price_eur"]
    positional = [ResearchStep(step_id=1, tool="group_by", columns=["Country"])]
    assert plan_columns(positional, WINE_COLUMNS) == ["country"]


def test_plan_columns_finds_names_with_spaces_and_brackets() -> None:
    from app.core.agent.quality_fixes import plan_columns

    columns = ["Принято (МСК)", "Сумма"]
    bucket = [_step(1, "time_bucket", date_column="Принято (МСК)", interval="week")]
    expression = [
        _step(
            1,
            "create_column",
            new_column="new",
            expression="\"Принято (МСК)\" >= DATE '2024-01-01'",
        )
    ]
    assert plan_columns(bucket, columns) == ["Принято (МСК)"]
    assert plan_columns(expression, columns) == ["Принято (МСК)"]


@pytest.fixture
def wines(db):
    from app.core.data.profiling import profile_table

    db.query(
        "CREATE TABLE wines AS SELECT * FROM (VALUES "
        "('Italy', 87, 120.0, DATE '2024-01-05'), ('France', 90, NULL, DATE '2024-03-01'), "
        "('Spain', 85, 15.0, NULL)) v(country, points, price, tasted)"
    )
    return profile_table(db, "wines", approx=False)


def test_plan_facts_report_nulls_only_in_plan_columns(db, wines) -> None:
    from app.core.agent.quality_fixes import plan_facts
    from app.core.llm.prompts.quality_rules import NULLS_HINT

    assert plan_facts(db, SHARE_PLAN, {}, wines, []) == (
        ["- в исходной таблице price: 1 из 3 без значения"],
        [NULLS_HINT],
    )
    average = [_step(1, "group_by", by=[], column="points", function="AVG")]
    assert plan_facts(db, average, {}, wines, []) == ([], [])
    assert plan_facts(db, SHARE_PLAN, {}, None, []) == ([], [])


def test_plan_facts_report_period_of_plan_dates(db, wines) -> None:
    from app.core.agent.quality_fixes import plan_facts

    bucket = [_step(1, "time_bucket", date_column="tasted", interval="month")]
    assert plan_facts(db, bucket, {}, wines, [])[0] == [
        "- в исходной таблице tasted: 1 из 3 без значения",
        "- в исходной таблице данные за период: 2024-01-05 – 2024-03-01 (tasted)",
    ]
    assert not any(
        "данные за период" in line for line in plan_facts(db, SHARE_PLAN, {}, wines, [])[0]
    )


async def test_insight_prompt_gets_plan_facts_from_passport(db, wines, monkeypatch) -> None:
    from app.core.agent.insight import InsightGenerator
    from app.core.memory.profile_memory import ProfileMemory

    monkeypatch.setattr(settings, "agent_quality_fixes", True)
    db.query("CREATE TABLE share AS SELECT 1 AS expensive, 3 AS total")
    generator = InsightGenerator(llm=_ScriptedLLM("Дорогих — 1 из 3"), db=db)
    await generator.generate("Доля дорогих?", "share", steps=SHARE_PLAN, source="wines")
    assert "без значения" not in generator.llm.prompts[0]

    ProfileMemory(db).save_profile(wines)
    generator = InsightGenerator(llm=_ScriptedLLM("Дорогих — 1 из 3"), db=db)
    await generator.generate("Доля дорогих?", "share", steps=SHARE_PLAN, source="wines")
    assert "- в исходной таблице price: 1 из 3 без значения" in generator.llm.prompts[0]
    assert "сколько строк\n  без значения" in generator.llm.prompts[0]


@pytest.mark.parametrize(
    ("descending", "limit", "facts"),
    [
        (True, 10, ["- ничья на вершине по value_ratio: значение 21.5 у 2 строк"]),
        (True, 1, ["- ничья на вершине по value_ratio: значение 21.5 у 2 строк"]),
        (False, 10, []),
    ],
)
def test_top_tie_counts_best_rows_of_sort_input(db, descending, limit, facts) -> None:
    from app.core.agent.quality_fixes import top_tie

    db.query(
        "CREATE TABLE ranked AS SELECT * FROM (VALUES ('Cramele Recas', 21.5), "
        "('Bandit NV Merlot', 21.5), ('Other', 20.0), ('Cheap', 3.0)) v(title, value_ratio)"
    )
    steps = [_step(3, "sort_limit", order_by="value_ratio", descending=descending, limit=limit)]
    assert top_tie(db, steps, {3: "ranked"}) == facts
    assert top_tie(db, steps, {}) == []


@pytest.mark.parametrize(
    ("start", "interval", "facts"),
    [
        ("2026-06-01", "week", ["- период 2026-09-28 неполный (данные по 2026-09-30)"]),
        ("2026-06-01", "month", []),
        ("2026-01-15", "month", ["- период 2026-01-01 неполный (данные с 2026-01-15)"]),
        ("2026-01-15", "day", []),
    ],
)
def test_partial_periods_at_the_edges_of_series(db, start, interval, facts) -> None:
    from app.core.agent.quality_fixes import partial_periods
    from app.core.data.profiling import profile_table

    db.query(
        f"CREATE TABLE sent AS SELECT * FROM (VALUES (DATE '{start}'), (DATE '2026-09-30')) v(day)"
    )
    profile = profile_table(db, "sent", approx=False)
    steps = [_step(1, "time_bucket", date_column="day", interval=interval)]
    assert partial_periods(steps, profile) == facts
    assert (
        partial_periods(
            [_step(1, "time_bucket", date_column="created", interval=interval)], profile
        )
        == []
    )


def test_partial_period_fact_needs_period_in_result(db) -> None:
    from app.core.agent.quality_fixes import plan_facts
    from app.core.data.profiling import profile_table
    from app.core.llm.prompts.quality_rules import PARTIAL_HINT

    db.query(
        "CREATE TABLE sent AS SELECT * FROM (VALUES (DATE '2026-06-01'), (DATE '2026-09-30')) v(d)"
    )
    profile = profile_table(db, "sent", approx=False)
    steps = [_step(1, "time_bucket", date_column="d", interval="week")]
    lines, hints = plan_facts(db, steps, {}, profile, ["period", "n"])
    assert "- период 2026-09-28 неполный (данные по 2026-09-30)" in lines
    assert PARTIAL_HINT in hints
    assert not any("неполный" in line for line in plan_facts(db, steps, {}, profile, ["n"])[0])
