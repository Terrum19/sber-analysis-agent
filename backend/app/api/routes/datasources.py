"""Подключение источников данных: CSV / PostgreSQL / ClickHouse.

Ключевое требование: подключение БЕЗ предобработки — сырые данные
грузятся в DuckDB, вся трансформация внутри платформы (см. core/data/).
Сразу после подключения таблицы профилируются (ingest.attach); если к
источнику есть описание — комментарии колонок или приложенный документ, —
названия и описания колонок переносятся в паспорт в фоне (ingest.describe).
"""

import logging
import shutil
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile

from app.api.deps import get_db
from app.api.schemas.datasource import DatasourceCreate, DatasourceOut, TableBrief
from app.config import settings
from app.core.data import ingest
from app.core.data.connectors.base import BaseConnector
from app.core.data.connectors.csv import CSVConnector, CSVLoadError, table_name
from app.core.data.connectors.factory import build_connector
from app.core.data.connectors.postgres import (
    PostgresConnectionError,
    PostgresExtensionUnavailableError,
)
from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.profiling import TableProfile
from app.core.memory.profile_memory import ProfileMemory

logger = logging.getLogger(__name__)
router = APIRouter()

UPLOAD_CHUNK = 1 << 20
DESCRIPTION_MAX_BYTES = 1 << 20


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


def read_description(upload: UploadFile | None) -> str | None:
    """Текст документа-описания (README, словарь колонок) или None."""
    if upload is None:
        return None
    data = upload.file.read(DESCRIPTION_MAX_BYTES + 1)
    if len(data) > DESCRIPTION_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Описание «{upload.filename}» больше {DESCRIPTION_MAX_BYTES >> 20} МБ.",
        )
    return data.decode("utf-8-sig", errors="replace")


async def describe_tables(
    connector: BaseConnector, db: DuckDBManager, tables: list[str], document: str | None
) -> None:
    """Фоновый перенос описания в паспорт: сбой не затрагивает подключение."""
    try:
        await ingest.describe(connector, db, tables, document)
    except Exception:
        logger.exception("Описание таблиц %s не перенесено в паспорт", tables)
        with db.lock:
            memory = ProfileMemory(db)
            for table in tables:
                if memory.load_description_status(table) == "pending":
                    memory.set_description_status(table, "failed")


def briefs(
    connector: BaseConnector,
    db: DuckDBManager,
    profiles: dict[str, TableProfile],
    document: str | None,
) -> list[TableBrief]:
    """Пометить pending таблицы с описанием и собрать сводки паспортов. Под db.lock."""
    ingest.mark_pending(connector, db, list(profiles), document)
    memory = ProfileMemory(db)
    return [
        TableBrief.from_profile(
            profile, memory.load_description_status(table), connector.warnings(table)
        )
        for table, profile in profiles.items()
    ]


@router.post("/csv", response_model=DatasourceOut)
def attach_csv(
    files: Annotated[list[UploadFile], File()],
    db: Annotated[DuckDBManager, Depends(get_db)],
    background: BackgroundTasks,
    description: Annotated[UploadFile | None, File()] = None,
) -> DatasourceOut:
    document = read_description(description)
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
            profiles = ingest.attach(connector, db)
            tables = list(profiles)
            summaries = briefs(connector, db, profiles, document)
    except CSVLoadError as error:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(error)) from None
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    background.add_task(describe_tables, connector, db, tables, document)
    return DatasourceOut(datasource_id=datasource_id, kind="csv", tables=tables, profiles=summaries)


@router.post("/db", response_model=DatasourceOut)
def attach_db(
    payload: DatasourceCreate,
    db: Annotated[DuckDBManager, Depends(get_db)],
    background: BackgroundTasks,
) -> DatasourceOut:
    try:
        connector = build_connector(payload.kind, payload.dsn, payload.db_schema)
        with db.lock:
            profiles = ingest.attach(connector, db)
            tables = list(profiles)
            summaries = briefs(connector, db, profiles, payload.description)
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
    background.add_task(describe_tables, connector, db, tables, payload.description)
    datasource_id = getattr(connector, "identity", f"{payload.kind}_{uuid4().hex[:10]}")
    return DatasourceOut(
        datasource_id=datasource_id, kind=payload.kind, tables=tables, profiles=summaries
    )
