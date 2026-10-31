"""Tools accept table and column names exactly as the sources have them."""

import pytest

from app.core.data.duckdb_manager import DuckDBManager
from app.core.tools.base import identifier
from app.core.tools.registry import tool_registry


@pytest.fixture
def db() -> DuckDBManager:
    manager = DuckDBManager(":memory:")
    manager.execute(
        """
        CREATE TABLE "Продажи аптек" AS SELECT * FROM (
            VALUES
                (101, 'Аптека 1', 3, 120.0, DATE '2024-01-01', 'визит'),
                (102, 'Аптека 2', 5, 80.0, DATE '2024-02-01', 'покупка'),
                (101, 'Аптека 1', 2, 150.0, DATE '2024-03-01', 'визит'),
                (102, 'Аптека 2', 7, 60.0, DATE '2024-04-01', 'покупка')
        ) AS rows("ID_Аптеки", "Код аптеки", "Сколько купили", "Цена, руб", "Дата продажи",
                  "Шаг")
        """
    )
    yield manager
    manager.close()


TABLE = "Продажи аптек"


def run(db: DuckDBManager, name: str, columns: list[str], **parameters: object) -> list[dict]:
    return db.to_records(tool_registry.get(name).run(db, TABLE, columns, **parameters))


def test_identifier_quotes_names_and_embedded_quotes() -> None:
    assert identifier("ID_Аптеки") == '"ID_Аптеки"'
    assert identifier('a"b') == '"a""b"'
    for value in ("", "a\x00b", None):
        with pytest.raises(ValueError):
            identifier(value)


@pytest.mark.parametrize(
    ("name", "columns", "parameters"),
    [
        ("select_columns", ["ID_Аптеки", "Код аптеки"], {}),
        ("filter_rows", ["Сколько купили", "3", ">"], {}),
        ("create_column", ["Выручка", '"Сколько купили" * "Цена, руб"'], {}),
        ("rename_column", ["Код аптеки", "Аптека"], {}),
        ("group_by", ["Код аптеки", "Сколько купили", "SUM"], {}),
        ("pivot", ["Сколько купили", "Код аптеки", "Шаг"], {}),
        ("time_bucket", ["Дата продажи", "month"], {}),
        ("lag", ["Сколько купили", "1", "Дата продажи"], {}),
        ("aggregate", ["ID_Аптеки"], {}),
        ("time_series", ["Дата продажи", "Сколько купили"], {}),
        ("pearson_corr", ["Сколько купили", "Цена, руб"], {}),
        ("t_test_ind", ["Цена, руб", "Код аптеки"], {}),
        ("anova_one_way", ["Цена, руб", "Код аптеки"], {}),
        ("describe_stats", ["Цена, руб"], {}),
        ("bootstrap_ci", ["Цена, руб"], {"n_bootstraps": 20}),
        ("chi_square", ["Код аптеки", "Шаг"], {}),
        ("search_categorical_values", ["Код аптеки", "аптека 1"], {}),
        ("inspect_temp_table", [], {}),
        ("funnel_steps", ["Шаг", "Дата продажи", "ID_Аптеки"], {}),
        ("retention_matrix", ["ID_Аптеки", "Дата продажи"], {}),
        ("revenue_analysis", ["Цена, руб", "Дата продажи", "Код аптеки"], {}),
        ("product_performance", ["Цена, руб", "Код аптеки"], {}),
        ("trend_analysis", ["Дата продажи", "Цена, руб"], {"seasonality_period": 4}),
        ("forecast", ["Дата продажи", "Цена, руб", "2"], {}),
        ("seasonality", ["Дата продажи", "Цена, руб"], {}),
        ("period_compare", ["Дата продажи", "Цена, руб", "month"], {}),
        ("lag_correlation", ["Дата продажи", "Сколько купили", "Цена, руб", "1"], {}),
        ("detect_anomalies", ["Цена, руб", "Код аптеки", "Дата продажи"], {"threshold": 0.1}),
    ],
)
def test_tools_accept_source_names(
    db: DuckDBManager, name: str, columns: list[str], parameters: dict
) -> None:
    assert run(db, name, columns, **parameters)


def test_derived_names_keep_the_source_column(db: DuckDBManager) -> None:
    grouped = run(db, "group_by", ["Код аптеки", "Сколько купили", "SUM"])
    assert {row["sum_Сколько купили"] for row in grouped} == {5, 12}
    lagged = run(db, "lag", ["Сколько купили", "1", "Дата продажи"])
    assert [row["lag_Сколько купили"] for row in lagged] == [None, 3, 5, 2]
