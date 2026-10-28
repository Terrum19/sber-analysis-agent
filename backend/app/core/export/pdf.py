"""Экспорт отчёта в PDF (Typst, фолбэк ReportLab).

Typst — быстрый компилятор отчётов из шаблона; ReportLab — фолбэк,
если Typst-рантайм недоступен. Выбор двигателя — на уровне фабрики.
"""


class PDFExporter:
    """Выгрузка инсайта + таблицы в .pdf."""

    def export(self, headline: str, details: list[str], records: list[dict], path: str) -> str:
        """Скомпилировать PDF-отчёт (TODO: Typst-шаблон, фолбэк ReportLab)."""
        raise NotImplementedError
