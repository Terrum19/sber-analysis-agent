"""Экспорт результатов исследования в делимые форматы.

Требование: «преобразовать исследование в файл, которым можно быстро
поделиться» — Excel (openpyxl) и PDF (ReportLab).
"""

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

router = APIRouter()
# result_ref попадает в SQL как имя таблицы — пускаем только temp_result_vN.
RESULT_REF = re.compile(r"temp_result_v\d+")


@router.post("/export")
async def export_artifact(
    payload: ExportRequest, db: Annotated[DuckDBManager, Depends(get_db)]
) -> FileResponse:
    """Сгенерировать .xlsx / .pdf по итогам исследования и отдать файл."""
    records: list[dict] = []
    if payload.result_ref:
        if not RESULT_REF.fullmatch(payload.result_ref):
            raise HTTPException(
                status_code=400,
                detail="result_ref должен быть ссылкой на temp_result_vN.",
            )
        try:
            records = db.to_records(payload.result_ref, limit=500)
        except duckdb.Error:
            records = []

    export_dir = Path(settings.storage_dir) / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)

    if payload.format == "xlsx":
        if not payload.result_ref or not RESULT_REF.fullmatch(payload.result_ref):
            raise HTTPException(
                status_code=400,
                detail="result_ref должен быть ссылкой на temp_result_vN.",
            )
        if not records:
            raise HTTPException(
                status_code=404,
                detail=f"Результат '{payload.result_ref}' не найден или пуст.",
            )
        filename = f"agent_export_{uuid.uuid4().hex[:8]}.xlsx"
        path = export_dir / filename
        ExcelExporter().export(records, str(path))
        return FileResponse(
            path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=filename,
        )

    # payload.format == "pdf"
    has_insight = bool(payload.question or payload.headline or payload.details or payload.spec)
    if not records and not has_insight:
        if payload.result_ref:
            raise HTTPException(
                status_code=404,
                detail=f"Результат '{payload.result_ref}' не найден или пуст.",
            )
        raise HTTPException(
            status_code=400,
            detail="Для экспорта в PDF необходимо указать result_ref или передать данные инсайта.",
        )

    chart_png = render_chart_png(payload.spec) if payload.spec else None
    filename = f"agent_export_{uuid.uuid4().hex[:8]}.pdf"
    path = export_dir / filename
    PdfExporter().export_answer(
        path=str(path),
        records=records,
        question=payload.question,
        headline=payload.headline,
        details=payload.details,
        chart_png=chart_png,
    )
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=filename,
    )
