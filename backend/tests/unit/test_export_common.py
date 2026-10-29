"""Тесты для модуля общих вспомогательных функций экспорта app.core.export.common."""

from decimal import Decimal
from unittest.mock import MagicMock

import duckdb

from app.core.dashboards.models import DashboardTile
from app.core.export.common import (
    extract_kpi_value,
    format_cell_value,
    format_kpi_number,
    resolve_tile_records,
)


def test_format_kpi_number_all_branches() -> None:
    # 1. Миллиарды
    assert format_kpi_number(1_500_000_000) == "1.5 млрд"
    assert format_kpi_number(Decimal(-2_300_000_000)) == "-2.3 млрд"

    # 2. Миллионы
    assert format_kpi_number(4_200_000) == "4.2 млн"
    assert format_kpi_number(Decimal(10_000_000)) == "10.0 млн"

    # 3. Тысячи (>= 10_000)
    assert format_kpi_number(15_000) == "15 000"
    assert format_kpi_number(Decimal(99_999)) == "99 999"

    # 4. Дробные числа < 10_000 с целым значением
    assert format_kpi_number(42.0) == "42"
    assert format_kpi_number(Decimal("75.000")) == "75"

    # 5. Дробные числа < 10_000 с нецелым значением
    assert format_kpi_number(42.5) == "42.50"
    assert format_kpi_number(Decimal("12.34")) == "12.34"

    # 6. Маленькие целые числа
    assert format_kpi_number(500) == "500"
    assert format_kpi_number(0) == "0"

    # 7. Булевы значения (не должны форматироваться как числа 1/0)
    assert format_kpi_number(True) == "True"
    assert format_kpi_number(False) == "False"

    # 8. None и строки
    assert format_kpi_number(None) == "—"
    assert format_kpi_number("не применимо") == "не применимо"


def test_extract_kpi_value_all_variants() -> None:
    # 1. Из списка словарей snapshot_data
    tile_list = DashboardTile(
        title="План продаж",
        is_kpi=True,
        snapshot_data=[{"revenue": 1200000}],
    )
    val, lbl = extract_kpi_value(tile_list)
    assert val == "1.2 млн"
    assert lbl == "План продаж"

    # 2. Из словаря с явными 'value' и 'label'
    tile_dict_explicit = DashboardTile(
        title="Дефолт",
        is_kpi=True,
        snapshot_data={"value": 500, "label": "Факт продаж"},
    )
    val, lbl = extract_kpi_value(tile_dict_explicit)
    assert val == "500"
    assert lbl == "Факт продаж"

    # 3. Из словаря без ключа 'value'
    tile_dict_implicit = DashboardTile(
        title="Метрика",
        is_kpi=True,
        snapshot_data={"custom_metric": 777},
    )
    val, lbl = extract_kpi_value(tile_dict_implicit)
    assert val == "777"
    assert lbl == "Метрика"

    # 4. Из spec.data.values
    tile_spec = DashboardTile(
        title="Спецификация",
        is_kpi=True,
        spec={"data": {"values": [{"count": 25}]}},
    )
    val, lbl = extract_kpi_value(tile_spec)
    assert val == "25"
    assert lbl == "Спецификация"

    # 5. Отсутствие данных
    tile_empty = DashboardTile(title="Пусто", is_kpi=True)
    val, lbl = extract_kpi_value(tile_empty)
    assert val == "—"
    assert lbl == "Пусто"


def test_resolve_tile_records_priorities_and_fallbacks() -> None:
    # Приоритет 1: Непустой snapshot_data
    tile_snap = DashboardTile(
        title="Снимок",
        snapshot_data=[{"id": 1}, {"id": 2}],
        result_table="t1",
    )
    db_mock = MagicMock()
    records = resolve_tile_records(tile_snap, db=db_mock, limit=1)
    assert records == [{"id": 1}]
    db_mock.to_records.assert_not_called()

    # Приоритет 2: Пустой snapshot_data = [] читает из DuckDB
    tile_empty_snap = DashboardTile(
        title="Из базы",
        snapshot_data=[],
        result_table="temp_result_v1",
    )
    db_mock.to_records.return_value = [{"col": "db_value"}]
    records = resolve_tile_records(tile_empty_snap, db=db_mock, limit=10)
    assert records == [{"col": "db_value"}]
    db_mock.to_records.assert_called_once_with("temp_result_v1", limit=10)

    # Приоритет 2: snapshot_data is None читает из DuckDB
    tile_none_snap = DashboardTile(
        title="Из базы None",
        snapshot_data=None,
        result_table="temp_result_v2",
    )
    db_mock.reset_mock()
    db_mock.to_records.return_value = [{"col": "db_value_2"}]
    records = resolve_tile_records(tile_none_snap, db=db_mock, limit=10)
    assert records == [{"col": "db_value_2"}]

    # Приоритет 2: Ошибка DuckDB не падает и передает управление spec
    tile_db_err = DashboardTile(
        title="Сбой базы",
        result_table="bad_table",
        spec={"data": {"values": [{"s": "from_spec"}]}},
    )
    db_err_mock = MagicMock()
    db_err_mock.to_records.side_effect = duckdb.Error("таблица не существует")
    records = resolve_tile_records(tile_db_err, db=db_err_mock)
    assert records == [{"s": "from_spec"}]

    # Приоритет 3: spec.data.values без базы
    tile_spec = DashboardTile(
        title="Только спека",
        spec={"data": {"values": [{"val": 10}, {"val": 20}]}},
    )
    records = resolve_tile_records(tile_spec, limit=1)
    assert records == [{"val": 10}]

    # Приоритет 4: Пустой список snapshot_data без базы и спеки
    tile_pure_empty = DashboardTile(title="Пустой список", snapshot_data=[])
    records = resolve_tile_records(tile_pure_empty)
    assert records == []

    # Приоритет 5: Ничего нет -> None
    tile_nothing = DashboardTile(title="Ничего")
    records = resolve_tile_records(tile_nothing)
    assert records is None


def test_format_cell_value_all_types() -> None:
    # None
    assert format_cell_value(None) == "—"

    # Booleans
    assert format_cell_value(True) == "Да"
    assert format_cell_value(False) == "Нет"

    # Numbers
    assert format_cell_value(1000000) == "1 000 000"
    assert format_cell_value(42.0) == "42"
    assert format_cell_value(42.56) == "42.56"
    assert format_cell_value(Decimal("100.0")) == "100"
    assert format_cell_value(Decimal("100.25")) == "100.25"

    # Strings
    assert format_cell_value("  Привет мир!  ") == "Привет мир!"
    long_str = "A" * 150
    truncated = format_cell_value(long_str, max_len=50)
    assert len(truncated) == 50
    assert truncated.endswith("…")
