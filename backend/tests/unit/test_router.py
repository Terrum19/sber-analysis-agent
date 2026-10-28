"""Быстрый роутер: шаблонные планы только для однозначных вопросов."""

from app.core.agent.router import resolve_category, resolve_date, resolve_metric, simple_plan
from app.core.data.profiling import ColumnStats, TableProfile


def profile() -> TableProfile:
    return TableProfile(
        table="sales",
        row_count=10,
        column_count=4,
        profiled_at="2026-10-28",
        columns=[
            ColumnStats("user_id", "VARCHAR", "identifier"),
            ColumnStats("amount", "DOUBLE", "numeric"),
            ColumnStats("event_date", "DATE", "temporal"),
            ColumnStats("region", "VARCHAR", "text"),
        ],
    )


TITLES = {
    "user_id": "Пользователь",
    "amount": "Сумма",
    "event_date": "Дата",
    "region": "Регион",
}


def test_count_rows_has_no_planner_cost() -> None:
    route = simple_plan("Сколько строк в таблице?", profile(), TITLES)
    assert route is not None
    assert route.kind == "simple_count"
    step = route.plan.steps[0]
    assert step.tool == "group_by"
    assert step.arguments == {"by": [], "function": "COUNT"}
    assert route.plan.final_viz == "kpi"


def test_total_and_average_pick_a_metric() -> None:
    total = simple_plan("Общая сумма amount", profile(), TITLES)
    avg = simple_plan("Средняя сумма", profile(), TITLES)
    assert total is not None and total.kind == "simple_total"
    assert total.plan.steps[0].arguments["column"] == "amount"
    assert avg is not None and avg.kind == "simple_avg"
    assert avg.plan.steps[0].arguments["function"] == "AVG"


def test_top_n_builds_group_by_and_sort_limit() -> None:
    route = simple_plan("Топ-5 пользователей по сумме", profile(), TITLES)
    assert route is not None
    assert route.kind == "simple_top"
    assert [step.tool for step in route.plan.steps] == ["group_by", "sort_limit"]
    assert route.plan.steps[0].arguments["by"] == ["user_id"]
    assert route.plan.steps[1].arguments == {
        "order_by": "sum_amount",
        "descending": True,
        "limit": 5,
    }


def test_time_series_uses_time_bucket() -> None:
    route = simple_plan("Сумма amount по месяцам", profile(), TITLES)
    assert route is not None
    assert route.kind == "simple_time"
    assert route.plan.steps[0].tool == "time_bucket"
    assert route.plan.steps[0].arguments["interval"] == "month"
    assert route.plan.steps[0].arguments["date_column"] == "event_date"
    assert route.plan.final_viz == "line"


def test_distribution_selects_one_column() -> None:
    route = simple_plan("Распределение amount", profile(), TITLES)
    assert route is not None
    assert route.kind == "simple_distribution"
    assert route.plan.steps[0].arguments == {"columns": ["amount"]}
    assert route.plan.final_viz == "histogram"


def test_filters_and_ambiguous_questions_go_to_llm() -> None:
    assert simple_plan("Сумма amount где регион = Москва", profile(), TITLES) is None
    assert simple_plan("Средняя сумма за 2024 год", profile(), TITLES) is None
    assert simple_plan("Сделай дашборд по сумме", profile(), TITLES) is None


def test_grouped_questions_are_not_misrouted() -> None:
    assert simple_plan("Сколько строк по регионам?", profile(), TITLES) is None
    assert simple_plan("Сумма amount по регионам", profile(), TITLES) is None


def test_unknown_metric_does_not_guess() -> None:
    # Две числовые колонки без совпадения — выбирать нельзя.
    prof = profile()
    prof.columns.append(ColumnStats("revenue", "DOUBLE", "numeric"))
    assert resolve_metric("Общая величина", prof, TITLES) is None
    assert resolve_category("Топ-5 по сумме", prof, TITLES) is None
    assert resolve_date("по месяцам", prof) == "event_date"
