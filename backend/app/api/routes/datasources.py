"""Подключение источников данных: CSV / PostgreSQL / ClickHouse.

Ключевое требование: подключение БЕЗ предобработки — сырые данные
грузятся в DuckDB, вся трансформация внутри платформы (см. core/data/).
"""

import shutil
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.deps import get_db
from app.api.schemas.datasource import DatasourceCreate, DatasourceOut
from app.config import settings
from app.core.data.connectors.csv import CSVConnector, CSVLoadError, table_name
from app.core.data.connectors.factory import build_connector
from app.core.data.connectors.postgres import (
    PostgresConnectionError,
    PostgresExtensionUnavailableError,
)
from app.core.data.duckdb_manager import DuckDBManager

router = APIRouter()

UPLOAD_CHUNK = 1 << 20


def store_upload(upload: UploadFile, target: Path, limit: int) -> None:
    written = 0
    with target.open("wb") as output:
        while chunk := upload.file.read(UPLOAD_CHUNK):
            written += len(chunk)
            if written > limit:
                raise HTTPException(
                    status_code=413,
                    detail=f"Файл «{upload.filename}» больше {settings.csv_max_upload_mb} МБ (CSV_MAX_UPLOAD_MB).",
                )
            output.write(chunk)


@router.get("/tables", response_model=list[str])
def list_tables(db: Annotated[DuckDBManager, Depends(get_db)]) -> list[str]:
    """Таблицы и представления пользователя без temp_result_vN и служебных схем."""
    return db.user_tables()


@router.post("/csv", response_model=DatasourceOut)
def attach_csv(
    files: Annotated[list[UploadFile], File()], db: Annotated[DuckDBManager, Depends(get_db)]
) -> DatasourceOut:
    datasource_id = f"csv_{uuid4().hex[:10]}"
    folder = Path(settings.storage_dir, "uploads", datasource_id).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    limit = settings.csv_max_upload_mb * 1024 * 1024
    paths: dict[str, str] = {}
    try:
        for upload in files:
            wanted = table_name(upload.filename or "")
            name = wanted
            suffix = 2
            while name in paths:
                name = f"{wanted}_{suffix}"
                suffix += 1
            target = folder / f"{name}.csv"
            store_upload(upload, target, limit)
            paths[name] = str(target)
        connector = CSVConnector(paths)
        with db.lock:
            tables = connector.connect(db)
    except CSVLoadError as error:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(error)) from None
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return DatasourceOut(datasource_id=datasource_id, kind="csv", tables=tables)


@router.post("/db", response_model=DatasourceOut)
def attach_db(
    payload: DatasourceCreate, db: Annotated[DuckDBManager, Depends(get_db)]
) -> DatasourceOut:
    try:
        connector = build_connector(payload.kind, payload.dsn, payload.db_schema)
        with db.lock:
            tables = connector.connect(db)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    except PostgresConnectionError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None
    except PostgresExtensionUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None
    except NotImplementedError:
        raise HTTPException(
            status_code=501, detail=f"Коннектор «{payload.kind}» пока не реализован."
        ) from None
    datasource_id = getattr(connector, "identity", f"{payload.kind}_{uuid4().hex[:10]}")
    return DatasourceOut(datasource_id=datasource_id, kind=payload.kind, tables=tables)
