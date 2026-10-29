"""Общие вспомогательные функции для экспорта отчётов (PDF и Excel).

Централизует:
- Разрешение записей плиток дашборда (resolve_tile_records) с единым приоритетом;
- Форматирование и извлечение KPI (format_kpi_number, extract_kpi_value);
- Форматирование значений ячеек (format_cell_value) с поддержкой bool, Decimal, float, int.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.dashboards.models import DashboardTile
    from app.core.data.duckdb_manager import DuckDBManager

logger = logging.getLogger(__name__)


def format_kpi_number(val: Any) -> str:
    """Форматировать числовое значение KPI в компактный человекочитаемый вид."""
    if isinstance(val, (int, float, Decimal)) and not isinstance(val, bool):
        v = float(val)
        abs_v = abs(v)
        if abs_v >= 1_000_000_000:
            return f"{v / 1_000_000_000:,.1f} млрд".replace(",", " ")
        if abs_v >= 1_000_000:
            return f"{v / 1_000_000:,.1f} млн".replace(",", " ")
        if abs_v >= 10_000:
            return f"{v:,.0f}".replace(",", " ")
        if isinstance(val, (float, Decimal)):
            if v.is_integer():
                return f"{int(v):,}".replace(",", " ")
            return f"{v:,.2f}".replace(",", " ")
        return f"{v:,.0f}".replace(",", " ")
    return str(val) if val is not None else "—"


def extract_kpi_value(tile: DashboardTile) -> tuple[str, str]:
    """Извлечь отображаемое значение и подпись для KPI-плитки."""
    label = tile.title
    data = tile.snapshot_data
    if isinstance(data, list) and data:
        row = data[0]
        if isinstance(row, dict) and row:
            val = next(iter(row.values()))
            return format_kpi_number(val), label
    elif isinstance(data, dict):
        val = data.get("value")
        if val is None:
            val = next(iter(data.values()), "—")
        lbl = data.get("label") or label
        return format_kpi_number(val), str(lbl)

    if isinstance(tile.spec, dict):
        spec_data = tile.spec.get("data")
        if isinstance(spec_data, dict):
            spec_values = spec_data.get("values")
            if isinstance(spec_values, list) and spec_values and isinstance(spec_values[0], dict):
                val = next(iter(spec_values[0].values()))
                return format_kpi_number(val), label

    return "—", label


def resolve_tile_records(
    tile: DashboardTile, db: DuckDBManager | None = None, limit: int = 500
) -> list[dict] | None:
    """Определить актуальный набор записей для плитки дашборда с единым приоритетом.

    Приоритет разрешения:
    1. Непустой `tile.snapshot_data` (готовый снимок строк).
    2. Таблица результатов `tile.result_table` в DuckDB (актуальные данные из базы,
       в том числе когда snapshot_data пуст или отсутствует).
    3. Инлайн-значения спецификации `tile.spec.data.values`.
    4. Пустой `tile.snapshot_data`, если ни базы, ни спеки нет.
    """
    records = tile.snapshot_data
    if isinstance(records, list) and records:
        return records[:limit]

    # Если snapshot_data пуст или отсутствует — читаем актуальную таблицу из DuckDB
    if tile.result_table and db is not None and hasattr(db, "to_records"):
        try:
            db_records = db.to_records(tile.result_table, limit=limit)
            if isinstance(db_records, list) and db_records:
                return db_records
        except Exception as exc:  # noqa: BLE001
            logger.debug("Не удалось прочитать таблицу результатов %s: %s", tile.result_table, exc)

    # Если в базе нет, пробуем spec.data.values
    if isinstance(tile.spec, dict):
        spec_data = tile.spec.get("data")
        if isinstance(spec_data, dict):
            values = spec_data.get("values")
            if isinstance(values, list) and values:
                return [r for r in values[:limit] if isinstance(r, dict)]

    if isinstance(records, list):
        return records

    return None


def format_cell_value(val: Any, max_len: int = 100) -> str:
    """Форматирование значений ячеек для презентабельного отображения.

    Корректно обрабатывает:
    - None -> "—"
    - bool -> "Да" / "Нет" (предотвращает превращение в 1 / 0)
    - float / Decimal -> с округлением и пробелами-разделителями тысяч
    - int -> с разделителями тысяч
    - строки -> усечение с многоточием при превышении max_len
    """
    if val is None:
        return "—"
    if isinstance(val, bool):
        return "Да" if val else "Нет"
    if isinstance(val, (int, float, Decimal)):
        if isinstance(val, (float, Decimal)):
            v = float(val)
            if v.is_integer():
                return f"{int(v):,}".replace(",", " ")
            return f"{v:,.2f}".replace(",", " ")
        return f"{val:,}".replace(",", " ")
    s = str(val).strip()
    if len(s) > max_len:
        s = s[: max(0, max_len - 1)] + "…"
    return s
