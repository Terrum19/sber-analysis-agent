"""Экспорт результатов исследования в делимые форматы.

Требование: «преобразовать исследование в файл, которым можно быстро
поделиться» — Excel (openpyxl) и PDF (ReportLab).
"""

import logging
import re
import uuid
from pathlib import Path
from typing import Annotated

import duckdb
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.api.deps import get_db
from app.api.schemas.chat import ExportRequest
from app.config import settings
from app.core.data.duckdb_manager import DuckDBManager
from app.core.export.charts import render_chart_png
from app.core.export.excel import ExcelExporter
from app.core.export.pdf import PdfExporter
from app.core.memory.session_memory import SessionMemory

router = APIRouter()
logger = logging.getLogger(__name__)
# result_ref попадает в SQL как имя таблицы — пускаем только temp_result_vN.
RESULT_REF = re.compile(r"temp_result_v\d+")


@router.post("/export")
def export_artifact(
    payload: ExportRequest, db: Annotated[DuckDBManager, Depends(get_db)]
) -> FileResponse:
    """Сгенерировать .xlsx / .pdf по итогам исследования и отдать файл."""
    if not payload.session_id or not payload.session_id.strip():
        raise HTTPException(
            status_code=400,
            detail="Поле session_id обязательно для экспорта.",
        )

    if payload.result_ref and not RESULT_REF.fullmatch(payload.result_ref):
        raise HTTPException(
            status_code=400,
            detail="result_ref должен быть ссылкой на temp_result_vN.",
        )

    has_insight = bool(payload.question or payload.headline or payload.details or payload.spec)

    if payload.format == "xlsx" and not payload.result_ref:
        raise HTTPException(
            status_code=400,
            detail="Для экспорта в Excel необходимо указать result_ref.",
        )

    if payload.format == "pdf" and not payload.result_ref and not has_insight:
        raise HTTPException(
            status_code=400,
            detail="Для экспорта в PDF необходимо указать result_ref или передать данные инсайта.",
        )

    memory = SessionMemory(payload.session_id, db)
    if not memory.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Сессия «{payload.session_id}» не найдена.",
        )

    records: list[dict] = []
    if payload.result_ref:
        if not memory.owns_result(payload.result_ref):
            raise HTTPException(
                status_code=404,
                detail=f"Результат «{payload.result_ref}» не найден в сессии «{payload.session_id}».",
            )
        limit = 50 if payload.format == "pdf" else 500
        try:
            records = db.to_records(payload.result_ref, limit=limit)
        except duckdb.Error as exc:
            raise HTTPException(
                status_code=404,
                detail=f"Не удалось прочитать результат «{payload.result_ref}»: {exc}",
            ) from exc
        if not records:
            raise HTTPException(
                status_code=404,
                detail=f"Результат «{payload.result_ref}» пуст.",
            )

    export_dir = Path(settings.storage_dir) / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)

    if payload.format == "xlsx":
        filename = f"agent_export_{uuid.uuid4().hex[:8]}.xlsx"
        path = export_dir / filename
        ExcelExporter().export(records, str(path))
        return FileResponse(
            path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=filename,
        )

    # payload.format == "pdf"
    chart_png = render_chart_png(payload.spec) if payload.spec else None
    filename = f"agent_export_{uuid.uuid4().hex[:8]}.pdf"
    path = export_dir / filename
    try:
        PdfExporter().export_answer(
            path=str(path),
            records=records,
            question=payload.question,
            headline=payload.headline,
            details=payload.details,
            chart_png=chart_png,
        )
    except RuntimeError as exc:
        logger.error("Ошибка при генерации PDF отчёта: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось сгенерировать PDF: {exc}",
        ) from exc
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=filename,
    )
