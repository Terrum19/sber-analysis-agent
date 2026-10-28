"""Unit-тесты семантического отбора колонок."""

from app.core.data.profiling import ColumnStats, TableProfile
from app.core.data.semantic_select import select_columns


def test_select_columns_top_k() -> None:
    cols = ["user_id", "amount", "region", "created_at"]
    picked = select_columns("средний amount по region", cols, top_k=2)
    assert len(picked) == 2
    assert "amount" in picked


def make_profile(kinds: dict[str, str]) -> TableProfile:
    columns = [ColumnStats(name=name, dtype="", kind=kind) for name, kind in kinds.items()]
    return TableProfile(
        table="t", row_count=1, column_count=len(columns), columns=columns, profiled_at=""
    )


PROFILE = make_profile(
    {"region": "text", "comment": "text", "created_at": "temporal", "amount": "numeric"}
)
NAMES = [column.name for column in PROFILE.columns]


def test_numeric_columns_first_for_aggregations() -> None:
    picked = select_columns("Какая средняя выручка?", NAMES, 1, PROFILE)
    assert picked == ["amount"]


def test_temporal_columns_first_for_trends() -> None:
    picked = select_columns("Покажи динамику продаж", NAMES, 1, PROFILE)
    assert picked == ["created_at"]


def test_name_match_beats_type_priority() -> None:
    picked = select_columns("сумма по region", NAMES, 2, PROFILE)
    assert picked == ["region", "amount"]


def test_without_profile_order_is_kept_for_ties() -> None:
    picked = select_columns("Какая средняя выручка?", ["region", "amount"], 1)
    assert picked == ["region"]


def test_identifier_columns_not_boosted_for_aggregations() -> None:
    profile = make_profile({"ID_Аптеки": "identifier", "Сколько_Купили": "numeric"})
    names = [column.name for column in profile.columns]
    picked = select_columns("Какое среднее количество?", names, 1, profile)
    assert picked == ["Сколько_Купили"]


def test_russian_question_finds_latin_columns_by_title_and_value() -> None:
    profile = make_profile({"client": "numeric", "revenue": "numeric", "category": "text"})
    profile.columns[2].top_values = [{"value": "Электроника", "count": 3}]
    names = [column.name for column in profile.columns]
    picked = select_columns(
        "Средняя выручка продаж электроники", names, 2, profile, titles={"revenue": "Выручка"}
    )
    assert picked == ["revenue", "category"]


def test_describe_columns_shows_ranges_and_spelling_variants() -> None:
    from app.core.data.semantic_select import describe_columns

    profile = make_profile({"revenue": "numeric", "category": "text", "date": "temporal"})
    revenue, category, date = profile.columns
    revenue.min, revenue.max, revenue.median = 5, 100, 28.5
    category.distinct = 2
    category.top_values = [
        {"value": "Электроника", "count": 3},
        {"value": "електроника", "count": 1},
    ]
    date.min_date, date.max_date = "2024-01-01", "2024-03-01"
    text = describe_columns(["revenue", "category", "date"], profile, {"revenue": "Сумма, руб."})
    assert "- revenue [numeric, ] — Сумма, руб.; от 5 до 100, медиана 28.5" in text
    assert '"Электроника", "електроника"' in text
    assert "с 2024-01-01 по 2024-03-01" in text


def test_describe_columns_omits_facts_the_profile_does_not_have() -> None:
    from app.core.data.semantic_select import describe_columns

    profile = make_profile({"revenue": "numeric", "date": "temporal", "flag": "boolean"})
    text = describe_columns(["revenue", "date", "flag"], profile, {})
    assert text.splitlines() == [
        "- revenue [numeric, ]",
        "- date [temporal, ]",
        "- flag [boolean, ]",
    ]


def test_describe_columns_shows_titles_from_passport() -> None:
    from app.core.data.semantic_select import describe_columns

    profile = make_profile({"revenue": "numeric", "flag": "boolean"})
    text = describe_columns(
        ["revenue", "flag"], profile, {"revenue": "Сумма"}, {"revenue": "Выручка"}
    )
    assert text.splitlines() == ["- revenue «Выручка» [numeric, ] — Сумма", "- flag [boolean, ]"]


def test_describe_columns_shows_quality_flags() -> None:
    from app.core.data.semantic_select import describe_columns

    profile = make_profile({"Q1A": "text", "region": "text"})
    profile.columns[0].flags = ["numeric_as_text", "null_tokens:6", "header_value"]
    text = describe_columns(["Q1A", "region"], profile)
    q1a, region = text.splitlines()
    assert q1a == (
        "- Q1A [text, ]; ⚠ числа записаны текстом, считай через TRY_CAST(... AS DOUBLE); "
        "⚠ литералы пропусков в тексте: 6; ⚠ среди значений есть имя колонки"
    )
    assert region == "- region [text, ]"


def test_other_columns_are_listed_by_name_title_and_kind() -> None:
    from app.core.data.semantic_select import describe_other_columns

    text = describe_other_columns(["comment", "created_at"], PROFILE, {"created_at": "Дата"})
    assert text == (
        "Остальные колонки таблицы (без выжимки): comment [text], created_at «Дата» [temporal]"
    )


def test_other_columns_are_capped_and_can_be_disabled() -> None:
    from app.core.data.semantic_select import describe_other_columns

    assert describe_other_columns(NAMES, PROFILE, limit=2).endswith("и ещё 2")
    assert describe_other_columns(NAMES, PROFILE, limit=0) == ""
    assert describe_other_columns([], PROFILE) == ""


def test_other_columns_without_profile_are_listed_by_name() -> None:
    from app.core.data.semantic_select import describe_other_columns

    text = describe_other_columns(["region", "ghost"], None, {"ghost": "Призрак"})
    assert text == "Остальные колонки таблицы (без выжимки): region, ghost «Призрак»"


def test_planner_prompt_lists_columns_outside_the_top_k(monkeypatch) -> None:
    import asyncio

    from app.config import settings

    monkeypatch.setattr(settings, "semantic_top_k", 1)

    from app.core.agent.planner import StepPlanner
    from app.models.research_step import ResearchPlan, ResearchStep

    class LLM:
        prompt = ""

        async def complete_structured(self, system, user, schema):
            LLM.prompt = user
            return ResearchPlan(
                steps=[ResearchStep(tool="aggregate", arguments={"column": "region"})]
            )

    context = {"columns": NAMES, "profile": PROFILE}
    asyncio.run(StepPlanner(llm=LLM()).plan("Какая средняя выручка?", context))
    others = LLM.prompt.split("Остальные колонки таблицы (без выжимки): ", 1)[1].split("\n")[0]
    assert {"region", "comment", "created_at"} <= {e.split(" ")[0] for e in others.split(", ")}
