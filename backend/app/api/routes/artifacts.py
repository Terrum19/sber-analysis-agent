"""Экспорт результатов исследования в делимые форматы.

Требование: «преобразовать исследование в файл, которым можно быстро
поделиться» — Excel (openpyxl) и PDF (ReportLab).
"""

import copy
import logging
import re
import uuid
from collections.abc import Callable
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
from app.core.export.charts import is_table_spec, render_chart_png
from app.core.export.excel import ExcelExporter
from app.core.export.pdf import PdfExporter
from app.core.memory.session_memory import SessionMemory

router = APIRouter()
logger = logging.getLogger(__name__)
# result_ref попадает в SQL как имя таблицы — пускаем только temp_result_vN.
RESULT_REF = re.compile(r"temp_result_v\d+")


def result_owner(payload: ExportRequest, db: DuckDBManager) -> Callable[[str], bool]:
    if payload.dashboard_id:
        dashboard = DashboardStore().get(payload.dashboard_id)
        if dashboard is None:
            raise HTTPException(
                status_code=404,
                detail=f"Дашборд «{payload.dashboard_id}» не найден.",
            )
        tables = {tile.result_table for tile in dashboard.tiles if tile.result_table}
        return lambda table: table in tables
    memory = SessionMemory(payload.session_id, db)
    if not memory.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Сессия «{payload.session_id}» не найдена.",
        )
    return memory.owns_result


@router.post("/export")
def export_artifact(
    payload: ExportRequest, db: Annotated[DuckDBManager, Depends(get_db)]
) -> FileResponse:
    """Сгенерировать .xlsx / .pdf по итогам исследования или дашборда и отдать файл."""
    if not payload.session_id or not payload.session_id.strip():
        raise HTTPException(
            status_code=400,
            detail="Поле session_id обязательно для экспорта.",
        )

    export_dir = Path(settings.storage_dir) / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)

    # 1. Экспорт дашборда целиком
    if payload.dashboard_id and not payload.result_ref:
        dashboard = DashboardStore().get(payload.dashboard_id)
        if dashboard is None:
            raise HTTPException(
                status_code=404,
                detail=f"Дашборд «{payload.dashboard_id}» не найден.",
            )
        if payload.title:
            dashboard.title = payload.title.strip()

        dash_title = (payload.title or dashboard.title or "dashboard").strip()
        safe_name = re.sub(r'[\\/*?:"<>|]+', "_", dash_title).strip() or "dashboard"
        filename = f"{safe_name}.{payload.format}"
        unique_filename = f"{safe_name}_{uuid.uuid4().hex[:8]}.{payload.format}"

        if payload.format == "pdf":
            path = export_dir / unique_filename
            try:
                PdfExporter().export_dashboard(
                    dashboard,
                    str(path),
                    db=db,
                    session_title=payload.session_title,
                    source_name=payload.source_name,
                    database_name=payload.database_name,
                )
            except Exception as exc:
                logger.error("Ошибка при генерации PDF дашборда: %s", exc)
                raise HTTPException(
                    status_code=500,
                    detail=f"Не удалось сгенерировать PDF: {exc}",
                ) from exc
            return FileResponse(
                path,
                media_type="application/pdf",
                filename=filename,
            )
        path = export_dir / unique_filename
        try:
            ExcelExporter().export_dashboard(
                dashboard,
                str(path),
                db=db,
                session_title=payload.session_title,
                source_name=payload.source_name,
                database_name=payload.database_name,
            )
        except Exception as exc:
            logger.error("Ошибка при генерации Excel дашборда: %s", exc)
            raise HTTPException(
                status_code=500,
                detail=f"Не удалось сгенерировать Excel: {exc}",
            ) from exc
        return FileResponse(
            path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=filename,
        )

    # 2. Экспорт отдельного исследования / плитки
    if payload.result_ref and not RESULT_REF.fullmatch(payload.result_ref):
        raise HTTPException(
            status_code=400,
            detail="result_ref должен быть ссылкой на temp_result_vN.",
        )

    has_insight = bool(payload.question or payload.headline or payload.details or payload.spec)

    if not payload.result_ref and not has_insight:
        format_name = "Excel" if payload.format == "xlsx" else "PDF"
        raise HTTPException(
            status_code=400,
            detail=f"Для экспорта в {format_name} необходимо указать result_ref или передать данные инсайта.",
        )

    owns_result = result_owner(payload, db)

    records: list[dict] = []
    if payload.result_ref:
        if not owns_result(payload.result_ref):
            owner_target = (
                f"дашборде «{payload.dashboard_id}»"
                if payload.dashboard_id
                else f"сессии «{payload.session_id}»"
            )
            raise HTTPException(
                status_code=404,
                detail=f"Результат «{payload.result_ref}» не найден в {owner_target}.",
            )
        limit = 50 if payload.format == "pdf" else 5000
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
    elif payload.spec:
        spec_data = payload.spec.get("data")
        if isinstance(spec_data, dict) and isinstance(spec_data.get("values"), list):
            records = [r for r in spec_data["values"] if isinstance(r, dict)]
        elif isinstance(spec_data, list):
            records = [r for r in spec_data if isinstance(r, dict)]
        elif isinstance(payload.spec.get("values"), list):
            records = [r for r in payload.spec["values"] if isinstance(r, dict)]

    export_title = (payload.title or payload.headline or "Аналитический отчёт").strip()
    safe_name = re.sub(r'[\\/*?:"<>|]+', "_", export_title).strip() or "agent_export"
    filename = f"{safe_name}.{payload.format}"
    unique_filename = f"{safe_name}_{uuid.uuid4().hex[:8]}.{payload.format}"

    if payload.format == "xlsx":
        path = export_dir / unique_filename
        try:
            ExcelExporter().export(
                records,
                str(path),
                title=export_title,
                question=payload.question,
                headline=payload.headline,
                details=payload.details,
                spec=payload.spec,
            )
        except Exception as exc:
            logger.error("Ошибка при генерации Excel отчёта: %s", exc)
            raise HTTPException(
                status_code=500,
                detail=f"Не удалось сгенерировать Excel: {exc}",
            ) from exc
        return FileResponse(
            path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=filename,
        )

    # payload.format == "pdf"
    is_table = is_table_spec(payload.spec) or (not payload.spec and bool(records))
    chart_spec = copy.deepcopy(payload.spec) if payload.spec else None
    if chart_spec and records:
        if chart_spec.get("usermeta", {}).get("chart_type") == "heatmap":
            from app.core.viz.vega_spec_builder import (
                build_vega_spec,
                detect_wide_matrix,
                infer_field_types,
            )

            if detect_wide_matrix(records, infer_field_types(records)) is not None:
                chart_spec = build_vega_spec("heatmap", data=records, title=payload.title)
            else:
                chart_spec["data"] = {"values": records}
        else:
            chart_spec["data"] = {"values": records}

    chart_png = None
    if not is_table and chart_spec:
        chart_png = render_chart_png(chart_spec)
    chart_render_failed = bool(chart_spec and not is_table and chart_png is None)

    path = export_dir / unique_filename
    try:
        PdfExporter().export_answer(
            path=str(path),
            records=records,
            question=payload.question,
            headline=payload.headline,
            details=payload.details,
            chart_png=chart_png,
            title=export_title,
            is_table=is_table,
            chart_render_failed=chart_render_failed,
        )
    except Exception as exc:
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
