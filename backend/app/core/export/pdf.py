"""Экспорт аналитического отчёта в PDF через ReportLab (Platypus).

Реализует формат двухстраничного бизнес-мемо (Executive Business Memo):
- Страница 1: Заголовок, вопрос, ключевые выводы (headline + details), график Vega-Lite.
- Страница 2: Табличные данные (до 50 строк) с корпоративной стилизацией.
"""

from __future__ import annotations

import datetime
import io
import logging
import os
from pathlib import Path
from typing import Any

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.fonts import addMapping
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    HRFlowable,
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)

_FONTS_REGISTERED = False
ACTIVE_FONT_NAME = "Helvetica"
ACTIVE_BOLD_FONT_NAME = "Helvetica-Bold"

FONT_SEARCH_PATHS = [
    # Linux
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
    (
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ),
    (os.environ.get("DEJAVU_FONT_PATH", ""), os.environ.get("DEJAVU_BOLD_FONT_PATH", "")),
    # macOS
    (
        "/Library/Fonts/DejaVuSans.ttf",
        "/Library/Fonts/DejaVuSans-Bold.ttf",
    ),
    (
        Path.home() / "Library/Fonts/DejaVuSans.ttf",
        Path.home() / "Library/Fonts/DejaVuSans-Bold.ttf",
    ),
    (
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ),
    (
        "/Library/Fonts/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    ),
]


def ensure_fonts_registered() -> tuple[str, str]:
    """Зарегистрировать шрифты DejaVu Sans с поддержкой кириллицы.

    Returns:
        Кортеж (имя_обычного_шрифта, имя_жирного_шрифта).
    """
    global _FONTS_REGISTERED, ACTIVE_FONT_NAME, ACTIVE_BOLD_FONT_NAME

    if _FONTS_REGISTERED:
        return ACTIVE_FONT_NAME, ACTIVE_BOLD_FONT_NAME

    regular_path: str | None = None
    bold_path: str | None = None

    for reg, bld in FONT_SEARCH_PATHS:
        reg_str = str(reg).strip()
        bld_str = str(bld).strip()
        if reg_str and os.path.isfile(reg_str):
            regular_path = reg_str
            bold_path = bld_str if bld_str and os.path.isfile(bld_str) else reg_str
            break

    if regular_path:
        try:
            pdfmetrics.registerFont(TTFont("DejaVu", regular_path))
            pdfmetrics.registerFont(TTFont("DejaVu-Bold", bold_path or regular_path))
            addMapping("DejaVu", 0, 0, "DejaVu")
            addMapping("DejaVu", 1, 0, "DejaVu-Bold")
            addMapping("DejaVu", 0, 1, "DejaVu")
            addMapping("DejaVu", 1, 1, "DejaVu-Bold")
            ACTIVE_FONT_NAME = "DejaVu"
            ACTIVE_BOLD_FONT_NAME = "DejaVu-Bold"
            logger.info("Шрифт DejaVu успешно зарегистрирован из %s", regular_path)
        except Exception as error:  # noqa: BLE001
            logger.warning("Не удалось зарегистрировать шрифт %s: %s", regular_path, error)
            ACTIVE_FONT_NAME = "Helvetica"
            ACTIVE_BOLD_FONT_NAME = "Helvetica-Bold"
    else:
        logger.warning(
            "Шрифт DejaVu Sans не найден на системе. Кириллица может отображаться некорректно."
        )
        ACTIVE_FONT_NAME = "Helvetica"
        ACTIVE_BOLD_FONT_NAME = "Helvetica-Bold"

    _FONTS_REGISTERED = True
    return ACTIVE_FONT_NAME, ACTIVE_BOLD_FONT_NAME


def sanitize_text(text: Any) -> str:
    """Очистить текст от символов, не поддерживаемых базовыми TrueType шрифтами (например, символ рубля U+20BD)."""
    if text is None:
        return ""
    s = str(text)
    return s.replace("₽", "руб.").replace("\u20bd", "руб.")


class NumberedCanvas(canvas.Canvas):
    """Канвас с двухпроходным расчётом общего числа страниц для футера 'Страница X из Y'."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._saved_page_states: list[dict[str, Any]] = []

    def showPage(self) -> None:
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_decorations(num_pages)
            super().showPage()
        super().save()

    def _draw_decorations(self, total_pages: int) -> None:
        self.saveState()
        font_name, _ = ensure_fonts_registered()
        self.setFont(font_name, 8)
        self.setFillColor(colors.HexColor("#64748B"))

        self.drawString(36, 22, "Sber AI BI Platform • Аналитический отчёт")
        page_str = f"Страница {self._pageNumber} из {total_pages}"
        self.drawRightString(A4[0] - 36, 22, page_str)

        self.setStrokeColor(colors.HexColor("#E2E8F0"))
        self.setLineWidth(0.5)
        self.line(36, 32, A4[0] - 36, 32)

        if self._pageNumber > 1:
            self.drawString(36, A4[1] - 25, "Sber AI BI Platform — Приложение с данными")
            self.drawRightString(
                A4[0] - 36, A4[1] - 25, datetime.datetime.now().strftime("%d.%m.%Y")
            )
            self.line(36, A4[1] - 28, A4[0] - 36, A4[1] - 28)

        self.restoreState()


class PdfExporter:
    """Генератор представительских PDF-отчётов по ответам аналитического агента."""

    def __init__(self) -> None:
        self.font_name, self.bold_font_name = ensure_fonts_registered()

    def export(
        self,
        headline: str,
        details: list[str],
        records: list[dict],
        path: str,
    ) -> str:
        """Совместимый метод экспорта (делегирует в export_answer)."""
        return self.export_answer(
            path=path,
            records=records,
            headline=headline,
            details=details,
        )

    def export_answer(
        self,
        path: str,
        records: list[dict] | None = None,
        question: str | None = None,
        headline: str | None = None,
        details: list[str] | None = None,
        chart_png: bytes | None = None,
        title: str = "Аналитический отчёт",
    ) -> str:
        """Скомпилировать PDF-отчёт формата Business Memo.

        Args:
            path: Путь для сохранения результирующего PDF файла.
            records: Табличные строки данных (будут ограничены первыми 50).
            question: Вопрос пользователя, на который отвечал агент.
            headline: Ключевой вывод инсайта.
            details: Маркированный список деталей инсайта.
            chart_png: Байты растрового графика Vega-Lite в PNG.
            title: Заголовок отчёта в шапке.

        Returns:
            Путь к сгенерированному файлу.
        """
        records = records or []
        details = [sanitize_text(d) for d in (details or [])]
        question = sanitize_text(question) if question else None
        headline = sanitize_text(headline) if headline else None
        title = sanitize_text(title)

        doc = SimpleDocTemplate(
            path,
            pagesize=A4,
            leftMargin=36,
            rightMargin=36,
            topMargin=36,
            bottomMargin=42,
        )
        printable_width = A4[0] - doc.leftMargin - doc.rightMargin  # 523.27 pt

        styles = self._build_styles()
        elements: list[Any] = []

        has_page1_content = bool(question or headline or details or chart_png)

        if has_page1_content:
            now_str = datetime.datetime.now().strftime("%d.%m.%Y, %H:%M")
            header_table = Table(
                [
                    [
                        Paragraph(f"<b>Sber AI BI</b> • {title}", styles["HeaderTitle"]),
                        Paragraph(now_str, styles["HeaderDate"]),
                    ]
                ],
                colWidths=[printable_width * 0.7, printable_width * 0.3],
            )
            header_table.setStyle(
                TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                        ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ]
                )
            )
            elements.append(header_table)
            elements.append(Spacer(1, 4))
            elements.append(
                HRFlowable(
                    width="100%",
                    thickness=1.5,
                    color=colors.HexColor("#0F172A"),
                    spaceBefore=2,
                    spaceAfter=10,
                )
            )

            if question:
                q_content = [
                    Paragraph("ВОПРОС ИССЛЕДОВАНИЯ", styles["MetaLabel"]),
                    Paragraph(question, styles["QuestionText"]),
                ]
                q_table = Table([[q_content]], colWidths=[printable_width])
                q_table.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
                            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                            ("LEFTPADDING", (0, 0), (-1, -1), 10),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                            ("TOPPADDING", (0, 0), (-1, -1), 8),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                        ]
                    )
                )
                elements.append(q_table)
                elements.append(Spacer(1, 10))

            if headline or details:
                insight_items: list[Any] = [Paragraph("КЛЮЧЕВЫЕ ВЫВОДЫ", styles["MetaLabelGreen"])]
                if headline:
                    insight_items.append(Paragraph(headline, styles["HeadlineText"]))
                if details:
                    insight_items.append(Spacer(1, 4))
                    for item in details:
                        bullet_text = f"• &nbsp; {item}"
                        insight_items.append(Paragraph(bullet_text, styles["DetailItem"]))

                insight_table = Table([[insight_items]], colWidths=[printable_width])
                insight_table.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F0FDF4")),
                            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#BBF7D0")),
                            ("LINELEFT", (0, 0), (0, -1), 3.0, colors.HexColor("#059669")),
                            ("LEFTPADDING", (0, 0), (-1, -1), 12),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                            ("TOPPADDING", (0, 0), (-1, -1), 8),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                        ]
                    )
                )
                elements.append(insight_table)
                elements.append(Spacer(1, 10))

            if chart_png:
                elements.append(Paragraph("Визуализация данных", styles["SectionHeading"]))
                elements.append(Spacer(1, 4))
                img_flowable = self._build_image(
                    chart_png, max_width=printable_width, max_height=230
                )
                if img_flowable:
                    elements.append(img_flowable)
                    elements.append(Spacer(1, 6))

            if records:
                elements.append(Spacer(1, 4))
                hint_text = (
                    "<i>Таблица с детальными показателями приведена на странице 2. "
                    "Для выгрузки полного массива данных используйте экспорт в Excel.</i>"
                )
                elements.append(Paragraph(hint_text, styles["Footnote"]))
                elements.append(PageBreak())

        # page 2
        if records:
            if not has_page1_content:
                elements.append(Paragraph(f"<b>Sber AI BI</b> • {title}", styles["HeaderTitle"]))
                elements.append(Spacer(1, 4))
                elements.append(
                    HRFlowable(
                        width="100%",
                        thickness=1.5,
                        color=colors.HexColor("#0F172A"),
                        spaceBefore=2,
                        spaceAfter=10,
                    )
                )

            elements.append(
                Paragraph("Таблица результатов (первые 50 строк выборки)", styles["SectionHeading"])
            )
            elements.append(Spacer(1, 6))

            table_flowable = self._build_data_table(records, printable_width, styles)
            if table_flowable:
                elements.append(table_flowable)

        if not elements:
            elements.append(Paragraph("Нет данных для отображения отчёта.", styles["NormalText"]))

        doc.build(elements, canvasmaker=NumberedCanvas)
        return path

    def _build_image(self, chart_png: bytes, max_width: float, max_height: float) -> Image | None:
        """Сформировать пропорционально масштабированный Platypus Image."""
        try:
            with PILImage.open(io.BytesIO(chart_png)) as img:
                orig_w, orig_h = img.size

            if orig_w <= 0 or orig_h <= 0:
                return None

            scale = min(max_width / orig_w, max_height / orig_h)
            target_w = orig_w * scale
            target_h = orig_h * scale
            return Image(io.BytesIO(chart_png), width=target_w, height=target_h)
        except Exception as error:  # noqa: BLE001
            logger.warning("Не удалось обработать изображение графика для PDF: %s", error)
            return None

    def _build_data_table(
        self, records: list[dict], printable_width: float, styles: dict[str, ParagraphStyle]
    ) -> Table | None:
        """Сформировать стилизованную таблицу до 50 строк выборки."""
        sample_rows = records[:50]
        if not sample_rows:
            return None

        headers = list(sample_rows[0].keys())
        col_count = len(headers)
        if col_count == 0:
            return None

        col_width = printable_width / col_count

        table_data: list[list[Paragraph]] = []

        header_row = [
            Paragraph(f"<b>{sanitize_text(col)}</b>", styles["TableHeaderCell"]) for col in headers
        ]
        table_data.append(header_row)

        for row in sample_rows:
            row_cells: list[Paragraph] = []
            for col in headers:
                val = row.get(col)
                cell_str = self._format_cell_value(val)
                row_cells.append(Paragraph(cell_str, styles["TableCell"]))
            table_data.append(row_cells)

        t = Table(table_data, colWidths=[col_width] * col_count, repeatRows=1)
        t_style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E293B")),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ]

        for i in range(1, len(table_data)):
            bg = colors.HexColor("#FFFFFF") if i % 2 == 1 else colors.HexColor("#F8FAFC")
            t_style.append(("BACKGROUND", (0, i), (-1, i), bg))

        t.setStyle(TableStyle(t_style))
        return t

    def _format_cell_value(self, val: Any) -> str:
        """Форматирование значений ячеек для деловой презентации."""
        if val is None:
            return "—"
        if isinstance(val, float):
            return f"{val:,.2f}".replace(",", " ")
        if isinstance(val, int):
            return f"{val:,}".replace(",", " ")
        return sanitize_text(str(val))

    def _build_styles(self) -> dict[str, ParagraphStyle]:
        """Подготовка типографических стилей документа."""
        base = getSampleStyleSheet()
        f_reg = self.font_name
        f_bold = self.bold_font_name

        return {
            "HeaderTitle": ParagraphStyle(
                "HeaderTitle",
                parent=base["Normal"],
                fontName=f_bold,
                fontSize=14,
                leading=16,
                textColor=colors.HexColor("#0F172A"),
            ),
            "HeaderDate": ParagraphStyle(
                "HeaderDate",
                parent=base["Normal"],
                fontName=f_reg,
                fontSize=9,
                leading=12,
                alignment=2,
                textColor=colors.HexColor("#64748B"),
            ),
            "MetaLabel": ParagraphStyle(
                "MetaLabel",
                parent=base["Normal"],
                fontName=f_bold,
                fontSize=8,
                leading=10,
                textColor=colors.HexColor("#64748B"),
                spaceAfter=3,
            ),
            "MetaLabelGreen": ParagraphStyle(
                "MetaLabelGreen",
                parent=base["Normal"],
                fontName=f_bold,
                fontSize=8,
                leading=10,
                textColor=colors.HexColor("#047857"),
                spaceAfter=3,
            ),
            "QuestionText": ParagraphStyle(
                "QuestionText",
                parent=base["Normal"],
                fontName=f_reg,
                fontSize=10,
                leading=13,
                textColor=colors.HexColor("#1E293B"),
            ),
            "HeadlineText": ParagraphStyle(
                "HeadlineText",
                parent=base["Normal"],
                fontName=f_bold,
                fontSize=12,
                leading=15,
                textColor=colors.HexColor("#064E3B"),
            ),
            "DetailItem": ParagraphStyle(
                "DetailItem",
                parent=base["Normal"],
                fontName=f_reg,
                fontSize=9,
                leading=12,
                textColor=colors.HexColor("#1F2937"),
            ),
            "SectionHeading": ParagraphStyle(
                "SectionHeading",
                parent=base["Normal"],
                fontName=f_bold,
                fontSize=11,
                leading=14,
                textColor=colors.HexColor("#0F172A"),
            ),
            "Footnote": ParagraphStyle(
                "Footnote",
                parent=base["Normal"],
                fontName=f_reg,
                fontSize=8,
                leading=11,
                textColor=colors.HexColor("#64748B"),
            ),
            "TableHeaderCell": ParagraphStyle(
                "TableHeaderCell",
                parent=base["Normal"],
                fontName=f_bold,
                fontSize=8,
                leading=10,
                textColor=colors.white,
            ),
            "TableCell": ParagraphStyle(
                "TableCell",
                parent=base["Normal"],
                fontName=f_reg,
                fontSize=7.5,
                leading=9.5,
                textColor=colors.HexColor("#1E293B"),
            ),
            "NormalText": ParagraphStyle(
                "NormalText",
                parent=base["Normal"],
                fontName=f_reg,
                fontSize=9,
                leading=12,
                textColor=colors.HexColor("#334155"),
            ),
        }
