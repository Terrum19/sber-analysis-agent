"""Экспорт исследований и дашбордов в Excel (openpyxl).

Поддерживает:
1. Одиночные исследования/таблицы: карточка запроса, фирменная палитра Сбера,
   автоширина колонок, числовые форматы, автофильтр, фиксация шапки,
   умный подбор и размещение диаграммы (BarChart, LineChart, PieChart, AreaChart).
2. Дашборды: титульный лист «Сводка» с крупными KPI-карточками, оглавлением
   и ссылками, а также отдельные листы под каждую плитку со снимком данных и графиком.
3. Защита от Formula Injection (CWE-1236) и валидация названий листов.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import openpyxl
from openpyxl.chart import AreaChart, BarChart, LineChart, PieChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.core.dashboards.models import Dashboard, DashboardTile
from app.core.data.duckdb_manager import DuckDBManager

# Фирменная палитра Сбера
SBER_GREEN = "088434"
SBER_LIGHT_GREEN = "21A038"
SBER_BG_MINT = "F0F7F1"
ZEBRA_FILL_COLOR = "F7FAF7"
BORDER_COLOR = "D9D9D9"
KPI_BORDER_COLOR = "D0DED1"
TEXT_MUTED = "555555"

FONT_NAME = "Calibri"

HEADER_FONT = Font(name=FONT_NAME, size=11, bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor=SBER_GREEN)

ZEBRA_FILL = PatternFill("solid", fgColor=ZEBRA_FILL_COLOR)
NO_FILL = PatternFill(fill_type=None)

THIN_BORDER_SIDE = Side(style="thin", color=BORDER_COLOR)
CELL_BORDER = Border(
    left=THIN_BORDER_SIDE,
    right=THIN_BORDER_SIDE,
    top=THIN_BORDER_SIDE,
    bottom=THIN_BORDER_SIDE,
)

KPI_BORDER_SIDE = Side(style="thin", color=KPI_BORDER_COLOR)
KPI_BORDER = Border(
    left=KPI_BORDER_SIDE,
    right=KPI_BORDER_SIDE,
    top=KPI_BORDER_SIDE,
    bottom=KPI_BORDER_SIDE,
)


def _sanitize_value(val: Any) -> Any:
    """Защита от CSV/Excel Formula Injection (CWE-1236) и подготовка типов."""
    if val is None:
        return ""
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float, Decimal, datetime, date)):
        return val
    if isinstance(val, (list, dict)):
        return json.dumps(val, ensure_ascii=False)

    s = str(val)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        # Если это нормальное число (например, "-42.5" или "+10"), возвращаем число
        try:
            if "." in s or "e" in s.lower():
                return float(s)
            return int(s)
        except ValueError:
            # Потенциальная формула или опасная строка — экранируем апострофом
            return "'" + s
    return s


def _safe_sheet_title(title: str, existing_titles: set[str]) -> str:
    """Очистить заголовок для имени листа Excel (до 31 символа, без спецсимволов)."""
    clean = re.sub(r"[\\/*?:\[\]]", " ", title).strip()
    if not clean:
        clean = "Лист"
    base = clean[:26].strip()
    candidate = base
    counter = 1
    while candidate.lower() in {t.lower() for t in existing_titles}:
        counter += 1
        candidate = f"{base[:23]}_{counter}"
    existing_titles.add(candidate)
    return candidate


def _format_kpi_number(val: Any) -> str:
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
        if isinstance(val, float):
            return f"{v:,.2f}".replace(",", " ")
        return f"{v:,.0f}".replace(",", " ")
    return str(val) if val is not None else "—"


def _extract_kpi_value(tile: DashboardTile) -> tuple[str, str]:
    """Извлечь отображаемое значение и подпись для KPI-плитки."""
    label = tile.title
    data = tile.snapshot_data
    if isinstance(data, list) and data:
        row = data[0]
        if isinstance(row, dict) and row:
            val = next(iter(row.values()))
            return _format_kpi_number(val), label
    elif isinstance(data, dict):
        val = data.get("value")
        if val is None:
            val = next(iter(data.values()), "—")
        lbl = data.get("label") or label
        return _format_kpi_number(val), lbl

    spec_values = tile.spec.get("data", {}).get("values")
    if isinstance(spec_values, list) and spec_values and isinstance(spec_values[0], dict):
        val = next(iter(spec_values[0].values()))
        return _format_kpi_number(val), label

    return "—", label


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)


def _detect_column_roles(records: list[dict], columns: list[str]) -> tuple[int, list[int]]:
    """Определить 1-based индекс категориальной колонки (X) и числовых метрик (Y)."""
    sample = records[:50]
    numeric_cols: list[int] = []
    text_or_date_cols: list[int] = []

    for idx, col in enumerate(columns, start=1):
        vals = [r.get(col) for r in sample if r.get(col) is not None]
        if not vals:
            continue
        num_count = sum(1 for v in vals if _is_number(v))
        if num_count / len(vals) >= 0.7:
            numeric_cols.append(idx)
        else:
            text_or_date_cols.append(idx)

    cat_col = text_or_date_cols[0] if text_or_date_cols else 1
    numeric_cols = [c for c in numeric_cols if c != cat_col]

    # Если есть несколько числовых колонок, исключаем явные ID / коды из метрик графика
    if len(numeric_cols) > 1:
        non_id = [
            c
            for c in numeric_cols
            if columns[c - 1].lower() not in ("id", "код", "номер", "№", "index")
            and not columns[c - 1].lower().endswith("_id")
        ]
        if non_id:
            numeric_cols = non_id

    # Ограничиваем количество серий до 5, чтобы график не захламлялся
    return cat_col, numeric_cols[:5]


def _detect_chart_type(spec: dict | None, category_col_name: str) -> str:
    """Определить тип диаграммы openpyxl ('bar', 'line', 'pie', 'area')."""
    if spec:
        usermeta = spec.get("usermeta") or {}
        explicit_type = (
            usermeta.get("chart_type") or usermeta.get("requested_type") or spec.get("chart_type")
        )
        if isinstance(explicit_type, str):
            t = explicit_type.lower()
            if t in ("line", "temporal"):
                return "line"
            if t in ("pie", "arc"):
                return "pie"
            if t in ("area",):
                return "area"
            if t in ("bar", "column"):
                return "bar"

        mark = spec.get("mark")
        mark_type = mark.get("type") if isinstance(mark, dict) else mark
        if isinstance(mark_type, str):
            mt = mark_type.lower()
            if mt in ("line", "trail"):
                return "line"
            if mt in ("arc",):
                return "pie"
            if mt in ("area",):
                return "area"
            if mt in ("bar",):
                return "bar"

    # Фолбэк по названию категориальной колонки
    cat_lower = category_col_name.lower()
    time_keywords = (
        "date",
        "дата",
        "месяц",
        "год",
        "квартал",
        "day",
        "день",
        "период",
        "time",
        "year",
        "month",
    )
    if any(k in cat_lower for k in time_keywords):
        return "line"
    return "bar"


def _build_chart(
    ws: Worksheet,
    cat_col: int,
    num_cols: list[int],
    header_row: int,
    last_row: int,
    chart_type: str = "bar",
    title: str | None = None,
) -> Any | None:
    """Создать и настроить объект диаграммы openpyxl."""
    if not num_cols or last_row <= header_row:
        return None

    if chart_type == "line":
        chart: Any = LineChart()
        chart.style = 13
    elif chart_type == "pie":
        chart = PieChart()
        chart.style = 10
    elif chart_type == "area":
        chart = AreaChart()
        chart.style = 11
    else:
        chart = BarChart()
        chart.type = "col"
        chart.grouping = "clustered"
        chart.style = 10

    if title:
        chart.title = title[:40]

    cats = Reference(ws, min_col=cat_col, min_row=header_row + 1, max_row=last_row)
    chart.set_categories(cats)

    # Для PieChart допускается только 1 серия данных
    cols_to_add = [num_cols[0]] if chart_type == "pie" else num_cols
    for col_idx in cols_to_add:
        data_ref = Reference(ws, min_col=col_idx, min_row=header_row, max_row=last_row)
        chart.add_data(data_ref, titles_from_data=True)

    chart.width = 16
    chart.height = 10
    return chart


def _auto_adjust_column_widths(
    ws: Worksheet, min_col: int = 1, max_col: int | None = None, min_row: int = 1
) -> None:
    """Автоматический расчёт ширины столбцов по длине содержимого с отступом."""
    if max_col is None:
        max_col = ws.max_column

    for col_idx in range(min_col, max_col + 1):
        max_len = 0
        col_letter = get_column_letter(col_idx)
        for row_idx in range(min_row, ws.max_row + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            if cell.value is not None:
                val_str = str(cell.value)
                for line in val_str.split("\n"):
                    max_len = max(max_len, len(line))
        # Учитываем пропорциональный шрифт и кириллицу
        width = min(max(int(max_len * 1.15) + 4, 11), 60)
        ws.column_dimensions[col_letter].width = width


class ExcelExporter:
    """Выгрузка результатов исследований и дашбордов в формат .xlsx."""

    def export(
        self,
        records: list[dict],
        path: str,
        title: str = "Отчёт",
        question: str | None = None,
        headline: str | None = None,
        details: list[str] | None = None,
        spec: dict | None = None,
    ) -> str:
        """Экспорт отдельного результата исследования (таблицы с диаграммой)."""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = _safe_sheet_title(title, set())

        cur_row = 1
        # Информационная карточка отчёта (если переданы метаданные исследования)
        if headline or question or details:
            if headline:
                cell = ws.cell(row=cur_row, column=1, value=headline)
                cell.font = Font(name=FONT_NAME, size=14, bold=True, color=SBER_GREEN)
                cur_row += 1

            if question:
                cell = ws.cell(row=cur_row, column=1, value=f"Вопрос: {question}")
                cell.font = Font(name=FONT_NAME, size=11, bold=True, color="333333")
                cur_row += 1

            if details:
                for d in details:
                    cell = ws.cell(row=cur_row, column=1, value=f"• {d}")
                    cell.font = Font(name=FONT_NAME, size=10, italic=True, color="444444")
                    cur_row += 1

            meta_text = (
                f"Сформировано: {datetime.now().strftime('%d.%m.%Y %H:%M')} | "
                f"Записей: {len(records)}"
            )
            cell = ws.cell(row=cur_row, column=1, value=meta_text)
            cell.font = Font(name=FONT_NAME, size=9, color=TEXT_MUTED)
            cur_row += 2  # Отступ перед таблицей

        if not records:
            empty_cell = ws.cell(row=cur_row, column=1, value="Нет данных для отображения")
            empty_cell.font = Font(name=FONT_NAME, size=11, italic=True, color=TEXT_MUTED)
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            wb.save(path)
            return path

        header_row = cur_row
        columns = list(records[0].keys())

        # Запись заголовков таблицы
        for col_idx, col_name in enumerate(columns, start=1):
            cell = ws.cell(row=header_row, column=col_idx, value=col_name)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = CELL_BORDER

        # Запись строк данных
        cur_row += 1
        for row_idx, r in enumerate(records):
            is_even = row_idx % 2 == 1
            row_fill = ZEBRA_FILL if is_even else NO_FILL

            for col_idx, col_name in enumerate(columns, start=1):
                raw_val = r.get(col_name)
                val = _sanitize_value(raw_val)
                cell = ws.cell(row=cur_row, column=col_idx, value=val)
                cell.fill = row_fill
                cell.border = CELL_BORDER
                cell.font = Font(name=FONT_NAME, size=11)

                if _is_number(val):
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                    if isinstance(val, (float, Decimal)):
                        cell.number_format = "#,##0.00"
                    else:
                        cell.number_format = "#,##0"
                elif isinstance(val, (datetime, date)):
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                    cell.number_format = "yyyy-mm-dd"
                else:
                    cell.alignment = Alignment(horizontal="left", vertical="center")

            cur_row += 1

        last_data_row = cur_row - 1

        # Закрепление шапки и автофильтр
        ws.freeze_panes = f"A{header_row + 1}"
        last_col_letter = get_column_letter(len(columns))
        ws.auto_filter.ref = f"A{header_row}:{last_col_letter}{last_data_row}"

        # Построение диаграммы
        cat_col, num_cols = _detect_column_roles(records, columns)
        cat_name = columns[cat_col - 1] if cat_col <= len(columns) else ""
        chart_type = _detect_chart_type(spec, cat_name)
        chart = _build_chart(
            ws=ws,
            cat_col=cat_col,
            num_cols=num_cols,
            header_row=header_row,
            last_row=last_data_row,
            chart_type=chart_type,
            title=title if title != "Отчёт" else None,
        )

        if chart is not None:
            # Размещение графика строго справа от таблицы с отступом в 2 колонки
            chart_col = get_column_letter(len(columns) + 2)
            ws.add_chart(chart, f"{chart_col}{header_row}")

        _auto_adjust_column_widths(ws, min_col=1, max_col=len(columns), min_row=header_row)

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        wb.save(path)
        return path

    def export_dashboard(
        self,
        dashboard: Dashboard,
        path: str,
        db: DuckDBManager | None = None,
    ) -> str:
        """Экспорт целостного дашборда в многостраничный .xlsx файл.

        Включает:
        - Лист «Сводка» с крупными KPI-карточками, реестром плиток и гиперссылками.
        - Отдельные листы под каждую плитку с исходными данными и графиком.
        """
        wb = openpyxl.Workbook()
        summary_ws = wb.active
        summary_ws.title = "Сводка"

        used_sheet_names: set[str] = {"Сводка"}

        # 1. Шапка листа «Сводка»
        cell = summary_ws.cell(row=1, column=1, value=dashboard.title)
        cell.font = Font(name=FONT_NAME, size=16, bold=True, color=SBER_GREEN)

        formatted_date = datetime.fromtimestamp(dashboard.created_at).strftime("%d.%m.%Y %H:%M")
        meta_str = (
            f"Дата среза: {formatted_date} | "
            f"Сессия: {dashboard.session_id} | "
            f"Всего блоков: {len(dashboard.tiles)}"
        )
        cell_meta = summary_ws.cell(row=2, column=1, value=meta_str)
        cell_meta.font = Font(name=FONT_NAME, size=10, color=TEXT_MUTED)

        cur_row = 4

        # 2. Блок KPI-карточек
        kpi_tiles = [
            t
            for t in dashboard.tiles
            if t.is_kpi or (isinstance(t.snapshot_data, dict) and "value" in t.snapshot_data)
        ]

        if kpi_tiles:
            kpi_title_cell = summary_ws.cell(
                row=cur_row, column=1, value="Ключевые показатели (KPI)"
            )
            kpi_title_cell.font = Font(name=FONT_NAME, size=12, bold=True, color=SBER_GREEN)
            cur_row += 1

            cards_per_row = 4
            card_col_width = 2  # Каждая карточка занимает 2 колонки

            for idx, tile in enumerate(kpi_tiles):
                grid_r = idx // cards_per_row
                grid_c = idx % cards_per_row

                start_col = 1 + grid_c * card_col_width
                end_col = start_col + card_col_width - 1
                card_top_row = cur_row + grid_r * 4

                val_str, label_str = _extract_kpi_value(tile)

                # Объединяем ячейки под значение
                summary_ws.merge_cells(
                    start_row=card_top_row,
                    start_column=start_col,
                    end_row=card_top_row + 1,
                    end_column=end_col,
                )
                val_cell = summary_ws.cell(row=card_top_row, column=start_col, value=val_str)
                val_cell.font = Font(name=FONT_NAME, size=18, bold=True, color=SBER_GREEN)
                val_cell.alignment = Alignment(horizontal="center", vertical="center")

                # Объединяем ячейки под подпись
                summary_ws.merge_cells(
                    start_row=card_top_row + 2,
                    start_column=start_col,
                    end_row=card_top_row + 2,
                    end_column=end_col,
                )
                lbl_cell = summary_ws.cell(row=card_top_row + 2, column=start_col, value=label_str)
                lbl_cell.font = Font(name=FONT_NAME, size=9, bold=False, color=TEXT_MUTED)
                lbl_cell.alignment = Alignment(horizontal="center", vertical="center")

                # Заливка и рамка карточки
                card_fill = PatternFill("solid", fgColor=SBER_BG_MINT)
                for r_idx in range(card_top_row, card_top_row + 3):
                    for c_idx in range(start_col, end_col + 1):
                        c = summary_ws.cell(row=r_idx, column=c_idx)
                        c.fill = card_fill
                        c.border = KPI_BORDER

            rows_taken = ((len(kpi_tiles) - 1) // cards_per_row + 1) * 4
            cur_row += rows_taken + 1

        # 3. Реестр плиток дашборда (Состав)
        cat_header_cell = summary_ws.cell(row=cur_row, column=1, value="Состав дашборда")
        cat_header_cell.font = Font(name=FONT_NAME, size=12, bold=True, color=SBER_GREEN)
        cur_row += 1

        headers = ["№", "Название плитки", "Тип", "Инсайт / Описание", "Переход к данным"]
        for col_idx, h_text in enumerate(headers, start=1):
            c = summary_ws.cell(row=cur_row, column=col_idx, value=h_text)
            c.font = HEADER_FONT
            c.fill = HEADER_FILL
            c.alignment = Alignment(horizontal="center", vertical="center")
            c.border = CELL_BORDER

        tile_start_row = cur_row
        cur_row += 1

        # Предварительный маппинг плиток на листы
        tile_sheet_map: dict[str, str | None] = {}
        tile_data_map: dict[str, list[dict] | None] = {}
        for tile in dashboard.tiles:
            records = tile.snapshot_data
            if not isinstance(records, list) and tile.result_table and db is not None:
                try:
                    records = db.to_records(tile.result_table, limit=5000)
                except (duckdb.Error, OSError, ValueError):
                    records = None

            if isinstance(records, list) and records:
                s_name = _safe_sheet_title(tile.title, used_sheet_names)
                tile_sheet_map[tile.id] = s_name
                tile_data_map[tile.id] = records
            else:
                tile_sheet_map[tile.id] = None
                tile_data_map[tile.id] = None

        for idx, tile in enumerate(dashboard.tiles, start=1):
            is_even = idx % 2 == 0
            r_fill = ZEBRA_FILL if is_even else NO_FILL

            type_label = (
                "KPI"
                if tile.is_kpi
                else (
                    tile.spec.get("usermeta", {}).get("chart_type")
                    or tile.spec.get("chart_type")
                    or "График"
                )
            )

            row_vals = [
                idx,
                tile.title,
                type_label,
                tile.insight or "—",
            ]

            for col_idx, val in enumerate(row_vals, start=1):
                c = summary_ws.cell(row=cur_row, column=col_idx, value=val)
                c.fill = r_fill
                c.border = CELL_BORDER
                c.font = Font(name=FONT_NAME, size=10)
                if col_idx == 1:
                    c.alignment = Alignment(horizontal="center", vertical="center")
                else:
                    c.alignment = Alignment(horizontal="left", vertical="center")

            # Колонка 5: ссылка на лист плитки
            target_sheet = tile_sheet_map.get(tile.id)
            c_link = summary_ws.cell(row=cur_row, column=5)
            c_link.fill = r_fill
            c_link.border = CELL_BORDER
            if target_sheet:
                c_link.value = "Открыть лист →"
                c_link.hyperlink = f"#'{target_sheet}'!A1"
                c_link.font = Font(name=FONT_NAME, size=10, color=SBER_GREEN, underline="single")
                c_link.alignment = Alignment(horizontal="center", vertical="center")
            else:
                c_link.value = "Сводка"
                c_link.font = Font(name=FONT_NAME, size=10, color=TEXT_MUTED)
                c_link.alignment = Alignment(horizontal="center", vertical="center")

            cur_row += 1

        _auto_adjust_column_widths(summary_ws, min_col=1, max_col=5, min_row=tile_start_row)

        # 4. Создание отдельных листов под плитки со снимками данных
        for tile in dashboard.tiles:
            target_sheet = tile_sheet_map.get(tile.id)
            records = tile_data_map.get(tile.id)
            if not target_sheet or not records:
                continue

            ws = wb.create_sheet(title=target_sheet)

            # Название плитки
            t_cell = ws.cell(row=1, column=1, value=tile.title)
            t_cell.font = Font(name=FONT_NAME, size=14, bold=True, color=SBER_GREEN)

            # Кнопка возврата в Сводку
            back_cell = ws.cell(row=2, column=1, value="← Вернуться в Сводку")
            back_cell.hyperlink = "#'Сводка'!A1"
            back_cell.font = Font(name=FONT_NAME, size=10, color=SBER_GREEN, underline="single")

            tile_cur_row = 3
            if tile.insight:
                ins_cell = ws.cell(row=tile_cur_row, column=1, value=f"Инсайт: {tile.insight}")
                ins_cell.font = Font(name=FONT_NAME, size=10, italic=True, color="444444")
                tile_cur_row += 1

            tile_cur_row += 1  # Отступ перед таблицей

            columns = list(records[0].keys())
            t_header_row = tile_cur_row

            # Заголовки таблицы плитки
            for col_idx, col_name in enumerate(columns, start=1):
                c = ws.cell(row=t_header_row, column=col_idx, value=col_name)
                c.font = HEADER_FONT
                c.fill = HEADER_FILL
                c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                c.border = CELL_BORDER

            tile_cur_row += 1
            for r_idx, r in enumerate(records):
                is_even = r_idx % 2 == 1
                row_fill = ZEBRA_FILL if is_even else NO_FILL

                for col_idx, col_name in enumerate(columns, start=1):
                    val = _sanitize_value(r.get(col_name))
                    c = ws.cell(row=tile_cur_row, column=col_idx, value=val)
                    c.fill = row_fill
                    c.border = CELL_BORDER
                    c.font = Font(name=FONT_NAME, size=11)

                    if _is_number(val):
                        c.alignment = Alignment(horizontal="right", vertical="center")
                        if isinstance(val, (float, Decimal)):
                            c.number_format = "#,##0.00"
                        else:
                            c.number_format = "#,##0"
                    elif isinstance(val, (datetime, date)):
                        c.alignment = Alignment(horizontal="center", vertical="center")
                        c.number_format = "yyyy-mm-dd"
                    else:
                        c.alignment = Alignment(horizontal="left", vertical="center")

                tile_cur_row += 1

            t_last_row = tile_cur_row - 1
            ws.freeze_panes = f"A{t_header_row + 1}"
            ws.auto_filter.ref = f"A{t_header_row}:{get_column_letter(len(columns))}{t_last_row}"

            # График для плитки
            cat_col, num_cols = _detect_column_roles(records, columns)
            cat_name = columns[cat_col - 1] if cat_col <= len(columns) else ""
            chart_type = _detect_chart_type(tile.spec, cat_name)
            chart = _build_chart(
                ws=ws,
                cat_col=cat_col,
                num_cols=num_cols,
                header_row=t_header_row,
                last_row=t_last_row,
                chart_type=chart_type,
                title=tile.title,
            )

            if chart is not None:
                chart_col = get_column_letter(len(columns) + 2)
                ws.add_chart(chart, f"{chart_col}{t_header_row}")

            _auto_adjust_column_widths(ws, min_col=1, max_col=len(columns), min_row=t_header_row)

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        wb.save(path)
        return path
