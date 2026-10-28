"""Экспорт результатов исследования в делимые форматы.

Требование: «преобразовать исследование в файл, которым можно быстро
поделиться» — Excel (openpyxl) и PDF (Typst/ReportLab).
"""

import asyncio
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
from app.core.dashboards.store import DashboardStore
from app.core.data.duckdb_manager import DuckDBManager
from app.core.export.excel import ExcelExporter

router = APIRouter()
# result_ref попадает в SQL как имя таблицы — пускаем только temp_result_vN.
RESULT_REF = re.compile(r"temp_result_v\d+")


@router.post("/export")
async def export_artifact(
    payload: ExportRequest, db: Annotated[DuckDBManager, Depends(get_db)]
) -> FileResponse:
    """Сгенерировать .xlsx / .pdf по итогам исследования или дашборда."""
    if payload.format == "pdf":
        raise HTTPException(
            status_code=501,
            detail="Экспорт PDF пока не реализован (core/export/pdf.py — TODO round-2). Попробуйте .xlsx.",
        )

    export_dir = Path(settings.storage_dir) / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    exporter = ExcelExporter()

    if payload.dashboard_id:
        dashboard = DashboardStore().get(payload.dashboard_id)
        if dashboard is None:
            raise HTTPException(
                status_code=404,
                detail=f"Дашборд '{payload.dashboard_id}' не найден.",
            )
        filename = f"dashboard_{payload.dashboard_id}_{uuid.uuid4().hex[:8]}.xlsx"
        path = export_dir / filename
        await asyncio.to_thread(exporter.export_dashboard, dashboard, str(path), db=db)
        return FileResponse(
            path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=filename,
        )

    if not payload.result_ref or not RESULT_REF.fullmatch(payload.result_ref):
        raise HTTPException(
            status_code=400,
            detail="Нужно указать result_ref (temp_result_vN) или dashboard_id.",
        )

    try:
        records = db.to_records(payload.result_ref, limit=5000)
    except duckdb.Error:
        records = []
    if not records:
        raise HTTPException(
            status_code=404,
            detail=f"Результат '{payload.result_ref}' не найден или пуст.",
        )

    filename = f"agent_export_{uuid.uuid4().hex[:8]}.xlsx"
    path = export_dir / filename
    await asyncio.to_thread(
        exporter.export,
        records,
        str(path),
        title="Результат исследования",
        question=payload.question,
        headline=payload.headline,
        details=payload.details,
        spec=payload.spec,
    )
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )
