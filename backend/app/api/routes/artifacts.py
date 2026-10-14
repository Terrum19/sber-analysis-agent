"""Экспорт результатов исследования в делимые форматы.

Требование: «преобразовать исследование в файл, которым можно быстро
поделиться» — Excel (openpyxl) и PDF (Typst/ReportLab).
"""
from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.api.schemas.chat import ExportRequest

router = APIRouter()


@router.post("/export")
async def export_artifact(payload: ExportRequest) -> FileResponse:
    """Сгенерировать .xlsx / .pdf по итогам исследования и отдать файл.

    TODO(round-2): ExcelExporter/PDFExporter из core/export/.
    """
    raise NotImplementedError
