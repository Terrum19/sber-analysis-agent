"""Экспорт исследования в Excel (openpyxl).

Требование: «преобразовать исследование в файл, которым можно быстро
поделиться и который везде читается» — Excel с таблицами + графиками.
"""

import openpyxl
from openpyxl.chart import BarChart, Reference


class ExcelExporter:
    """Выгрузка результата исследования в .xlsx."""

    def export(self, records: list[dict], path: str, title: str = "Отчёт") -> str:
        """Записать данные + встроенную столбчатую диаграмму в файл."""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = title
        if records:
            ws.append(list(records[0].keys()))
            for row in records:
                ws.append(list(row.values()))
            chart = BarChart()
            data = Reference(ws, min_col=2, min_row=1, max_row=len(records) + 1)
            cats = Reference(ws, min_col=1, min_row=2, max_row=len(records) + 1)
            chart.add_data(data, titles_from_data=True)
            chart.set_categories(cats)
            ws.add_chart(chart, "F2")
        wb.save(path)
        return path
