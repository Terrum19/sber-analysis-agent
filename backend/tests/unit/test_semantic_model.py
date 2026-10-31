"""Semantic model: валидация, хранение, YAML."""

from pathlib import Path

import pytest

from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.semantic_model import SemanticRegistry
from app.models.semantic import SemanticDimension, SemanticMetric, SemanticModel


def model() -> SemanticModel:
    return SemanticModel(
        table="sales",
        description="Продажи",
        metrics=[
            SemanticMetric(
                name="revenue",
                title="Выручка",
                aggregation="SUM",
                column="amount",
                synonyms=["выручка", "доход"],
            ),
            SemanticMetric(name="orders", title="Заказы", aggregation="COUNT"),
        ],
        dimensions=[
            SemanticDimension(
                name="month",
                column="event_date",
                kind="temporal",
                time_grains=["month", "quarter"],
            ),
            SemanticDimension(name="region", column="region", kind="categorical"),
        ],
        default_time_dimension="month",
    )


@pytest.fixture
def db() -> DuckDBManager:
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE sales AS SELECT DATE '2024-01-01' AS event_date, "
        "'msk' AS region, 10.0 AS amount"
    )
    yield manager
    manager.close()


def test_validate_accepts_a_consistent_model(db: DuckDBManager) -> None:
    registry = SemanticRegistry(db)
    assert registry.validate(model(), {"event_date", "region", "amount"}) == []


def test_validate_reports_unknown_columns_and_bad_default(db: DuckDBManager) -> None:
    broken = model()
    broken.metrics[0].column = "missing"
    broken.dimensions[1].column = "missing_region"
    broken.default_time_dimension = "region"
    errors = SemanticRegistry(db).validate(broken, {"event_date", "region", "amount"})
    assert any("missing" in error for error in errors)
    assert any("default_time_dimension" in error for error in errors)


def test_save_load_and_delete(db: DuckDBManager) -> None:
    registry = SemanticRegistry(db)
    registry.save(model())
    loaded = registry.load("sales")
    assert loaded is not None
    assert loaded.metrics[0].name == "revenue"
    assert loaded.version == 1
    registry.delete("sales")
    assert registry.load("sales") is None


def test_yaml_round_trip(db: DuckDBManager, tmp_path: Path) -> None:
    registry = SemanticRegistry(db)
    path = tmp_path / "sales.semantic.yml"
    registry.save_yaml(model(), path)
    loaded = registry.load_yaml(path)
    assert loaded.metrics[0].column == "amount"
    assert loaded.dimensions[0].time_grains == ["month", "quarter"]


def test_load_yaml_rejects_unknown_column(db: DuckDBManager, tmp_path: Path) -> None:
    payload = model().model_dump(mode="json")
    payload["metrics"][0]["column"] = "nope"
    path = tmp_path / "bad.yml"
    path.write_text(__import__("yaml").safe_dump(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="nope"):
        SemanticRegistry(db).load_yaml(path)
