"""Экспорт аналитического отчёта в PDF через ReportLab (Platypus).

Реализует формат двухстраничного бизнес-мемо (Executive Business Memo):
- Страница 1: Заголовок, вопрос, ключевые выводы (headline + details), график Vega-Lite.
- Страница 2: Табличные данные (до 50 строк) с корпоративной стилизацией.
"""

from __future__ import annotations

import copy
import datetime
import io
import logging
import os
import re
import xml.sax.saxutils
from decimal import Decimal
from pathlib import Path
from typing import Any

from PIL import Image as PILImage
from reportlab.graphics.shapes import Circle, Drawing, PolyLine
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
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.config import settings
from app.core.dashboards.models import Dashboard, DashboardTile
from app.core.data.duckdb_manager import DuckDBManager
from app.core.export.charts import render_chart_png

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
        except Exception as error:
            logger.error("Не удалось зарегистрировать шрифт %s: %s", regular_path, error)
            raise RuntimeError(
                f"Ошибка регистрации шрифта DejaVu из {regular_path}: {error}"
            ) from error
    else:
        logger.error(
            "Шрифт DejaVu Sans не найден в системе. Кириллический текст в отчётах PDF будет испорчен. "
            "Установите пакет fonts-dejavu-core (Linux) или задайте переменную окружения DEJAVU_FONT_PATH."
        )
        raise RuntimeError(
            "Шрифт с поддержкой кириллицы (DejaVu Sans) не найден в системе. "
            "Для экспорта отчётов в PDF установите пакет fonts-dejavu-core (Linux: apt-get install -y fonts-dejavu-core) "
            "или укажите путь через переменную окружения DEJAVU_FONT_PATH."
        )

    _FONTS_REGISTERED = True
    return ACTIVE_FONT_NAME, ACTIVE_BOLD_FONT_NAME


# Пределы длины полей для предотвращения LayoutError в ReportLab
MAX_QUESTION_LENGTH = 1500
MAX_HEADLINE_LENGTH = 1000
MAX_DETAIL_ITEM_LENGTH = 1000
MAX_DETAILS_COUNT = 30
MAX_CELL_LENGTH = 50
MAX_TITLE_LENGTH = 150
MAX_COLUMN_NAME_LENGTH = 80
MAX_TABLE_COLUMNS = 10


def truncate_text(text: Any, max_length: int) -> str:
    """Обрезать слишком длинный текст с добавлением многоточия для предотвращения LayoutError."""
    if text is None:
        return ""
    s = str(text).strip()
    if len(s) <= max_length:
        return s
    return s[: max_length - 3].rstrip() + "..."


def sanitize_text(text: Any) -> str:
    """Очистить текст от символов, не поддерживаемых базовыми TrueType шрифтами,
    и экранировать специальные XML-символы (<, >, &) для безопасной вставки в ReportLab Paragraph.
    """
    if text is None:
        return ""
    s = str(text).replace("₽", "руб.").replace("\u20bd", "руб.")
    return xml.sax.saxutils.escape(s)


class NumberedCanvas(canvas.Canvas):
    """Канвас с двухпроходным расчётом общего числа страниц для футера 'Страница X из Y'."""

    report_title: str = "Sber AI BI Platform • Аналитический отчёт"

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

        page_w, page_h = getattr(self, "_pagesize", A4)
        doc_title = getattr(self, "report_title", "Sber AI BI Platform • Аналитический отчёт")

        self.drawString(36, 22, doc_title)
        page_str = f"Страница {self._pageNumber} из {total_pages}"
        self.drawRightString(page_w - 36, 22, page_str)

        self.setStrokeColor(colors.HexColor("#E2E8F0"))
        self.setLineWidth(0.5)
        self.line(36, 32, page_w - 36, 32)

        if self._pageNumber > 1:
            self.drawString(36, page_h - 25, doc_title)
            self.drawRightString(
                page_w - 36, page_h - 25, datetime.datetime.now().strftime("%d.%m.%Y")
            )
            self.line(36, page_h - 28, page_w - 36, page_h - 28)

        self.restoreState()


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

    def _derive_substantive_insight(
        self,
        records: list[dict],
        headline: str | None,
        details: list[str] | None,
        title: str,
    ) -> tuple[str | None, list[str]]:
        """Сформировать содержательные аналитические выводы без дублирования названия отчёта."""
        clean_title = (title or "").strip().lower()
        clean_h = (headline or "").strip()

        # Если заголовок вывода совпадает с общим названием документа — сбрасываем его
        if clean_h.lower() == clean_title:
            clean_h = ""

        clean_d = [
            str(d).strip()
            for d in (details or [])
            if d and str(d).strip() and str(d).strip().lower() != clean_title
        ]

        # Если есть детали, но нет заголовка инсайта — первая деталь становится заголовком
        if not clean_h and clean_d:
            clean_h = clean_d[0]
            clean_d = clean_d[1:]

        # Если у нас уже есть содержательный инсайт — возвращаем его
        if clean_h:
            return clean_h, clean_d

        # Если инсайта нет, автоматически извлекаем аналитические показатели из данных выборки
        if records:
            sample = records[0]
            num_cols: list[str] = []
            lbl_cols: list[str] = []
            for k in sample:
                if any(
                    isinstance(r.get(k), (int, float)) and not isinstance(r.get(k), bool)
                    for r in records[:10]
                ):
                    num_cols.append(k)
                else:
                    lbl_cols.append(k)

            if num_cols:
                # Берём ключевую числовую метрику (последняя числовая колонка, например, выручка или count/n)
                num_col = num_cols[-1]
                lbl_col = lbl_cols[0] if lbl_cols else None
                valid_rows = [r for r in records if isinstance(r.get(num_col), (int, float))]
                if valid_rows:
                    total_val = sum(r[num_col] for r in valid_rows)
                    max_row = max(valid_rows, key=lambda r: r[num_col])
                    min_row = min(valid_rows, key=lambda r: r[num_col])
                    max_val_str = _format_kpi_number(max_row[num_col])
                    min_val_str = _format_kpi_number(min_row[num_col])

                    max_lbl = (
                        str(max_row.get(lbl_col, ""))
                        if lbl_col
                        else f"Строка {records.index(max_row) + 1}"
                    )
                    min_lbl = (
                        str(min_row.get(lbl_col, ""))
                        if lbl_col
                        else f"Строка {records.index(min_row) + 1}"
                    )

                    share_str = (
                        f" ({max_row[num_col] / total_val * 100:.1f}% от общего объёма)"
                        if total_val > 0 and len(valid_rows) > 1
                        else ""
                    )
                    derived_headline = (
                        f"Лидирующая позиция — «{max_lbl}»: {max_val_str}{share_str}."
                    )
                    derived_details: list[str] = [
                        f"Суммарное значение показателя «{num_col}»: {_format_kpi_number(total_val)} по {len(records)} строкам выборки."
                    ]
                    if len(valid_rows) > 1 and max_lbl != min_lbl:
                        derived_details.append(
                            f"Минимальное значение отмечено у «{min_lbl}»: {min_val_str}."
                        )
                    return derived_headline, derived_details

            derived_headline = f"Выборка содержит {len(records)} записей для детального анализа."
            metric_names = ", ".join(list(sample.keys())[:5])
            return derived_headline, [f"Анализируемые показатели: {metric_names}."]

        return None, []

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
        title_raw = title
        title = sanitize_text(truncate_text(title, MAX_TITLE_LENGTH))
        question = sanitize_text(truncate_text(question, MAX_QUESTION_LENGTH)) if question else None

        d_headline, d_details = self._derive_substantive_insight(
            records=records,
            headline=headline,
            details=details,
            title=title_raw,
        )
        headline = (
            sanitize_text(truncate_text(d_headline, MAX_HEADLINE_LENGTH)) if d_headline else None
        )
        details = [
            sanitize_text(truncate_text(d, MAX_DETAIL_ITEM_LENGTH))
            for d in (d_details or [])[:5]
            if d and str(d).strip()
        ]

        doc = SimpleDocTemplate(
            path,
            pagesize=A4,
            leftMargin=36,
            rightMargin=36,
            topMargin=36,
            bottomMargin=36,
        )
        printable_width = A4[0] - doc.leftMargin - doc.rightMargin  # 523.27 pt

        styles = self._build_styles()
        elements: list[Any] = []

        has_page1_content = bool(question or headline or details or chart_png)

        if has_page1_content:
            elements.extend(self._build_header(title, printable_width, styles))

            if question:
                q_paragraphs = [p.strip() for p in question.split("\n") if p.strip()]
                q_rows = [[Paragraph("ВОПРОС ИССЛЕДОВАНИЯ", styles["MetaLabel"])]]
                for p_text in q_paragraphs:
                    q_rows.append([Paragraph(p_text, styles["QuestionText"])])

                q_table = Table(q_rows, colWidths=[printable_width])
                q_table.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
                            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                            ("LEFTPADDING", (0, 0), (-1, -1), 10),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                            ("TOPPADDING", (0, 0), (-1, -1), 3),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                            ("TOPPADDING", (0, 0), (-1, 0), 8),
                            ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
                        ]
                    )
                )
                elements.append(q_table)
                elements.append(Spacer(1, 10))

            if headline or details:
                insight_rows = [[Paragraph("КЛЮЧЕВЫЕ ВЫВОДЫ", styles["MetaLabelGreen"])]]
                if headline:
                    insight_rows.append([Paragraph(headline, styles["HeadlineText"])])
                if details:
                    for item in details:
                        bullet_text = f"• &nbsp; {item}"
                        insight_rows.append([Paragraph(bullet_text, styles["DetailItem"])])

                insight_table = Table(insight_rows, colWidths=[printable_width])
                insight_table.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F0FDF4")),
                            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#BBF7D0")),
                            ("LINELEFT", (0, 0), (0, -1), 3.0, colors.HexColor("#059669")),
                            ("LEFTPADDING", (0, 0), (-1, -1), 12),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                            ("TOPPADDING", (0, 0), (-1, -1), 3),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                            ("TOPPADDING", (0, 0), (-1, 0), 8),
                            ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
                        ]
                    )
                )
                elements.append(insight_table)
                elements.append(Spacer(1, 10))

            if chart_png:
                elements.append(Paragraph("Визуализация данных", styles["SectionHeading"]))
                elements.append(Spacer(1, 4))
                img_flowable = self._build_image(
                    chart_png, max_width=printable_width, max_height=210
                )
                if img_flowable:
                    elements.append(img_flowable)
                    elements.append(Spacer(1, 6))

            if records:
                elements.append(Spacer(1, 4))
                hint_text = "<i>Таблица с детальными показателями приведена в приложении ниже.</i>"
                elements.append(Paragraph(hint_text, styles["Footnote"]))
                elements.append(PageBreak())

        # page 2
        if records:
            max_table_height = 715.0
            candidate_rows = records[:50]
            table_flowable = None
            while candidate_rows:
                t = self._build_data_table(candidate_rows, printable_width, styles)
                if t is None:
                    break
                _w, h = t.wrap(printable_width, 10000)
                if h <= max_table_height or len(candidate_rows) <= 3:
                    table_flowable = t
                    break
                candidate_rows = candidate_rows[: max(1, len(candidate_rows) - 2)]

            elements.append(
                Paragraph(
                    f"Таблица результатов (первые {len(candidate_rows)} строк выборки)",
                    styles["SectionHeading"],
                )
            )
            elements.append(Spacer(1, 6))

            if table_flowable:
                elements.append(table_flowable)

        if not elements:
            elements.append(Paragraph("Нет данных для отображения отчёта.", styles["NormalText"]))

        title_trunc = truncate_text(title, 40)
        canvas_class = type(
            "AnswerCanvas",
            (NumberedCanvas,),
            {"report_title": f"Sber AI BI Platform • {title_trunc}"},
        )
        doc.build(elements, canvasmaker=canvas_class)
        return path

    def _resolve_dashboard_meta(
        self,
        dashboard: Dashboard,
        db: DuckDBManager | None = None,
        session_title: str | None = None,
        source_name: str | None = None,
    ) -> tuple[str | None, str | None]:
        """Определить человекочитаемое название сессии и имя источника данных."""
        resolved_title = session_title.strip() if session_title and session_title.strip() else None
        resolved_source = source_name.strip() if source_name and source_name.strip() else None

        if dashboard.session_id and (not resolved_title or not resolved_source):
            try:
                from app.core.memory.session_memory import SessionMemory

                memory = SessionMemory(dashboard.session_id, db=db)
                if memory.exists():
                    if not resolved_title and memory.title:
                        resolved_title = memory.title.strip()
                    if not resolved_source:
                        ds_id = memory.datasource_id
                        if ds_id and ds_id.startswith("csv_"):
                            upload_folder = Path(settings.storage_dir) / "uploads" / ds_id
                            if upload_folder.is_dir():
                                csvs = sorted(p.name for p in upload_folder.glob("*.csv"))
                                if csvs:
                                    resolved_source = ", ".join(csvs)
                        if not resolved_source and ds_id:
                            from app.core.memory.datasource_store import DatasourceStore

                            rec = DatasourceStore().get(ds_id)
                            if rec:
                                resolved_source = rec.description or rec.identity or rec.db_schema
                        if not resolved_source and memory.table:
                            resolved_source = memory.table.strip()
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Не удалось извлечь метаданные сессии %s: %s", dashboard.session_id, exc
                )

        if not resolved_source:
            for tile in dashboard.tiles:
                if tile.result_table and not tile.result_table.startswith("temp_result_"):
                    resolved_source = tile.result_table
                    break

        # Если название сессии представляет собой UUID или хеш — не выводим его
        if resolved_title and re.fullmatch(r"^[0-9a-fA-F-]{20,}$", resolved_title):
            resolved_title = None

        return resolved_title, resolved_source

    def export_dashboard(
        self,
        dashboard: Dashboard,
        path: str,
        db: DuckDBManager | None = None,
        session_title: str | None = None,
        source_name: str | None = None,
    ) -> str:
        """Скомпилировать представительский PDF-отчёт по дашборду.

        Args:
            dashboard: Экземпляр модели дашборда с плитками.
            path: Путь для сохранения PDF файла.
            db: Менеджер DuckDB для чтения связанных таблиц (при необходимости).
            session_title: Название сессии диалога (если известно).
            source_name: Название файла данных или базы данных (если известно).

        Returns:
            Путь к сгенерированному файлу.
        """
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

        resolved_stitle, resolved_source = self._resolve_dashboard_meta(
            dashboard=dashboard,
            db=db,
            session_title=session_title,
            source_name=source_name,
        )

        # Фирменная шапка дашборда
        elements.extend(
            self._build_dashboard_header(
                title=dashboard.title,
                session_id=dashboard.session_id,
                created_at=dashboard.created_at,
                tile_count=len(dashboard.tiles),
                printable_width=printable_width,
                styles=styles,
                session_title=resolved_stitle,
                source_name=resolved_source,
            )
        )

        kpi_tiles = [
            t
            for t in dashboard.tiles
            if t.is_kpi or (isinstance(t.snapshot_data, dict) and "value" in t.snapshot_data)
        ]
        other_tiles = [t for t in dashboard.tiles if t not in kpi_tiles]

        # Блок ключевых показателей (KPI)
        if kpi_tiles:
            elements.append(Paragraph("КЛЮЧЕВЫЕ ПОКАЗАТЕЛИ (KPI)", styles["MetaLabelGreen"]))
            elements.append(Spacer(1, 4))
            elements.extend(self._build_kpi_grid(kpi_tiles, printable_width, styles))
            elements.append(Spacer(1, 10))

        # Визуализации и аналитические блоки
        if other_tiles:
            elements.append(Paragraph("АНАЛИТИЧЕСКИЕ ВИЗУАЛИЗАЦИИ И ДАННЫЕ", styles["MetaLabel"]))
            elements.append(Spacer(1, 6))

            for tile in other_tiles:
                card_elements = self._build_tile_card(
                    tile=tile,
                    printable_width=printable_width,
                    styles=styles,
                    db=db,
                )
                if card_elements:
                    elements.append(KeepTogether(card_elements))
                    elements.append(Spacer(1, 8))

        if not kpi_tiles and not other_tiles:
            elements.append(
                Paragraph("В данном дашборде пока нет добавленных плиток.", styles["NormalText"])
            )

        title_trunc = truncate_text(dashboard.title, 40)
        canvas_class = type(
            "DashboardCanvas",
            (NumberedCanvas,),
            {"report_title": f"Sber AI BI Platform • {title_trunc}"},
        )

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        doc.build(elements, canvasmaker=canvas_class)
        return path

    def _build_dashboard_header(
        self,
        title: str,
        session_id: str | None = None,
        created_at: float | None = None,
        tile_count: int = 0,
        printable_width: float = 523.27,
        styles: dict[str, ParagraphStyle] | None = None,
        session_title: str | None = None,
        source_name: str | None = None,
    ) -> list[Any]:
        """Сформировать фирменную шапку дашборда с логотипом Сбера и метаданными."""
        if styles is None:
            styles = self._build_styles()

        now_str = (
            datetime.datetime.fromtimestamp(created_at).strftime("%d.%m.%Y, %H:%M")
            if created_at
            else datetime.datetime.now().strftime("%d.%m.%Y, %H:%M")
        )
        clean_title = sanitize_text(truncate_text(title, MAX_TITLE_LENGTH))

        meta_parts: list[str] = []
        if session_title and session_title.strip():
            clean_stitle = sanitize_text(truncate_text(session_title.strip(), 40))
            if clean_stitle and clean_stitle.lower() != clean_title.lower():
                meta_parts.append(f"Сессия: {clean_stitle}")

        if source_name and source_name.strip():
            clean_source = sanitize_text(truncate_text(source_name.strip(), 40))
            if clean_source:
                meta_parts.append(f"Источник: {clean_source}")

        meta_parts.append(f"Всего плиток: {tile_count}")
        meta_str = " &nbsp;•&nbsp; ".join(meta_parts)

        header_table = Table(
            [
                [
                    self._build_logo(size=18),
                    Paragraph(f"<b>Sber AI BI</b> • {clean_title}", styles["HeaderTitle"]),
                    Paragraph(now_str, styles["HeaderDate"]),
                ],
                [
                    "",
                    Paragraph(meta_str, styles["MetaSubtitle"]),
                    "",
                ],
            ],
            colWidths=[24, printable_width - 24 - 110, 110],
        )
        header_table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        return [
            header_table,
            Spacer(1, 4),
            HRFlowable(
                width="100%",
                thickness=1.5,
                color=colors.HexColor("#088434"),
                spaceBefore=2,
                spaceAfter=10,
            ),
        ]

    def _build_kpi_grid(
        self,
        kpi_tiles: list[DashboardTile],
        printable_width: float,
        styles: dict[str, ParagraphStyle],
    ) -> list[Any]:
        """Сформировать компактную сетку KPI-карточек (до 3 в ряд)."""
        elements: list[Any] = []
        cards_per_row = 3
        gap = 8.0

        for row_start in range(0, len(kpi_tiles), cards_per_row):
            batch = kpi_tiles[row_start : row_start + cards_per_row]
            k = len(batch)
            card_w = (printable_width - (k - 1) * gap) / k

            row_cells: list[Any] = []
            col_widths: list[float] = []

            for idx, tile in enumerate(batch):
                val_str, lbl_str = _extract_kpi_value(tile)
                val_clean = sanitize_text(truncate_text(val_str, 30))
                lbl_clean = sanitize_text(truncate_text(lbl_str, 50))

                v_para = Paragraph(f"<b>{val_clean}</b>", styles["KpiValue"])
                l_para = Paragraph(lbl_clean, styles["KpiLabel"])

                card_table = Table([[v_para], [l_para]], colWidths=[card_w])
                card_table.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F0FDF4")),
                            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#BBF7D0")),
                            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                            ("TOPPADDING", (0, 0), (-1, -1), 8),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                            ("LEFTPADDING", (0, 0), (-1, -1), 4),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                        ]
                    )
                )

                row_cells.append(card_table)
                col_widths.append(card_w)

                if idx < k - 1:
                    row_cells.append("")
                    col_widths.append(gap)

            outer_table = Table([row_cells], colWidths=col_widths)
            outer_table.setStyle(
                TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 0),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                        ("TOPPADDING", (0, 0), (-1, -1), 0),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                    ]
                )
            )
            elements.append(outer_table)
            elements.append(Spacer(1, 6))

        return elements

    def _build_tile_card(
        self,
        tile: DashboardTile,
        printable_width: float,
        styles: dict[str, ParagraphStyle],
        db: DuckDBManager | None = None,
    ) -> list[Any]:
        """Сформировать блок одной аналитической плитки (заголовок, инсайт, график/таблица)."""
        card: list[Any] = []

        # Заголовок плитки
        clean_title = sanitize_text(truncate_text(tile.title, MAX_TITLE_LENGTH))
        card.append(Paragraph(clean_title, styles["SectionHeading"]))
        card.append(Spacer(1, 4))

        # Ключевой вывод (инсайт), если есть
        if tile.insight and str(tile.insight).strip():
            ins_text = sanitize_text(truncate_text(tile.insight, MAX_HEADLINE_LENGTH))
            ins_table = Table(
                [[Paragraph(f"• &nbsp; <b>Вывод:</b> {ins_text}", styles["DetailItem"])]],
                colWidths=[printable_width],
            )
            ins_table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F0FDF4")),
                        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#BBF7D0")),
                        ("LINELEFT", (0, 0), (0, -1), 3.0, colors.HexColor("#059669")),
                        ("LEFTPADDING", (0, 0), (-1, -1), 10),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ]
                )
            )
            card.append(ins_table)
            card.append(Spacer(1, 6))

        # Извлекаем строки данных
        records = tile.snapshot_data
        if not isinstance(records, list) and tile.result_table and db is not None:
            try:
                records = db.to_records(tile.result_table, limit=500)
            except Exception as err:  # noqa: BLE001
                logger.debug("Не удалось прочитать таблицу %s: %s", tile.result_table, err)
                records = None
        if not isinstance(records, list) and isinstance(tile.spec, dict):
            spec_data = tile.spec.get("data")
            if isinstance(spec_data, dict) and isinstance(spec_data.get("values"), list):
                records = spec_data["values"]

        # Рендеринг графика (Vega-Lite)
        chart_png: bytes | None = None
        is_table_type = bool(
            tile.spec
            and isinstance(tile.spec, dict)
            and tile.spec.get("usermeta", {}).get("chart_type") == "table"
        )

        if not is_table_type and tile.spec and isinstance(tile.spec, dict):
            spec_to_render = copy.deepcopy(tile.spec)
            if isinstance(records, list) and records:
                s_data = spec_to_render.get("data")
                if not isinstance(s_data, dict) or not isinstance(s_data.get("values"), list):
                    spec_to_render["data"] = {"values": records[:500]}
            chart_png = render_chart_png(spec_to_render, width=640, scale=2.0)

        if chart_png:
            img_flowable = self._build_image(chart_png, max_width=printable_width, max_height=230)
            if img_flowable:
                card.append(img_flowable)
                card.append(Spacer(1, 6))
        elif isinstance(records, list) and records:
            # Табличное представление
            table_flowable = self._build_data_table(records[:20], printable_width, styles)
            if table_flowable:
                card.append(table_flowable)
                card.append(Spacer(1, 6))

        return card

    def _build_logo(self, size: float = 18.0) -> Drawing:
        """Сформировать фирменный векторный логотип Сбера (зелёный круг с белой галочкой)."""
        d = Drawing(size, size)
        cx = size / 2.0
        cy = size / 2.0
        r = size / 2.0
        # Фирменный зелёный круг Сбера
        d.add(Circle(cx, cy, r, fillColor=colors.HexColor("#21A038"), strokeColor=None))
        # Белая галочка
        scale = size / 20.0
        d.add(
            PolyLine(
                [
                    (5.5 * scale, 9.5 * scale),
                    (9.0 * scale, 6.0 * scale),
                    (14.5 * scale, 14.0 * scale),
                ],
                strokeColor=colors.white,
                strokeWidth=2.0 * scale,
                strokeLineCap=1,
                strokeLineJoin=1,
            )
        )
        return d

    def _build_header(
        self, title: str, printable_width: float, styles: dict[str, ParagraphStyle]
    ) -> list[Any]:
        """Сформировать фирменную шапку отчёта с логотипом Сбера и датой."""
        now_str = datetime.datetime.now().strftime("%d.%m.%Y, %H:%M")
        header_table = Table(
            [
                [
                    self._build_logo(size=18),
                    Paragraph(f"<b>Sber AI BI</b> • {title}", styles["HeaderTitle"]),
                    Paragraph(now_str, styles["HeaderDate"]),
                ]
            ],
            colWidths=[24, printable_width - 24 - 110, 110],
        )
        header_table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        return [
            header_table,
            Spacer(1, 4),
            HRFlowable(
                width="100%",
                thickness=1.5,
                color=colors.HexColor("#0F172A"),
                spaceBefore=2,
                spaceAfter=10,
            ),
        ]

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

        headers = list(sample_rows[0].keys())[:MAX_TABLE_COLUMNS]
        col_count = len(headers)
        if col_count == 0:
            return None

        col_width = printable_width / col_count

        table_data: list[list[Paragraph]] = []

        header_row = [
            Paragraph(
                f"<b>{sanitize_text(truncate_text(col, MAX_COLUMN_NAME_LENGTH))}</b>",
                styles["TableHeaderCell"],
            )
            for col in headers
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
            ("TOPPADDING", (0, 0), (-1, -1), 2.0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.0),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
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
        s = truncate_text(val, MAX_CELL_LENGTH)
        return sanitize_text(s)

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
                keepWithNext=True,
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
            "KpiValue": ParagraphStyle(
                "KpiValue",
                parent=base["Normal"],
                fontName=f_bold,
                fontSize=16,
                leading=19,
                alignment=1,
                textColor=colors.HexColor("#088434"),
            ),
            "KpiLabel": ParagraphStyle(
                "KpiLabel",
                parent=base["Normal"],
                fontName=f_reg,
                fontSize=8.5,
                leading=11,
                alignment=1,
                textColor=colors.HexColor("#64748B"),
            ),
            "MetaSubtitle": ParagraphStyle(
                "MetaSubtitle",
                parent=base["Normal"],
                fontName=f_reg,
                fontSize=8.5,
                leading=11,
                textColor=colors.HexColor("#64748B"),
            ),
        }
