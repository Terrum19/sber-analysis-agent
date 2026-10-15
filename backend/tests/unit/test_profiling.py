"""Unit-тесты семантического отбора колонок."""

from app.core.data.semantic_select import select_columns


def test_select_columns_top_k() -> None:
    cols = ["user_id", "amount", "region", "created_at"]
    picked = select_columns("средний amount по region", cols, top_k=2)
    assert len(picked) == 2
    assert "amount" in picked
