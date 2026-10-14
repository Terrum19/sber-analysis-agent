"""Unit-тесты морфинга графиков без нового вызова LLM."""
from app.core.viz.chart_morph import morph


def test_morph_bar_to_line() -> None:
    spec = morph("bar", "line", [{"x": 1, "y": 2}])
    assert spec["mark"] == "line"


def test_morph_illegal_falls_back() -> None:
    spec = morph("scatter", "bar", [{"x": 1, "y": 2}])
    assert spec["mark"] in {"bar", "line"}
