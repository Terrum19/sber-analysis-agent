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
