"""Подключение источников данных: CSV / PostgreSQL / ClickHouse.

Ключевое требование: подключение БЕЗ предобработки — сырые данные
грузятся в DuckDB, вся трансформация внутри платформы (см. core/data/).
Сразу после подключения таблицы профилируются (ingest.attach); если к
источнику есть описание — комментарии колонок или приложенный документ, —
названия и описания колонок переносятся в паспорт в фоне (ingest.describe).
Источник БД запоминается и переподключается после рестарта (core/data/restore.py).
CSV-источник — это файлы в storage/uploads/<id> и постоянные таблицы DuckDB,
помеченные комментарием «csv:<id>/<файл>»; DELETE удаляет и то и другое.
"""

import logging
import re
import shutil
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import duckdb
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile

from app.api.deps import get_db
from app.api.schemas.datasource import DatasourceCreate, DatasourceOut, TableBrief
from app.config import settings
from app.core.data import ingest, restore
from app.core.data.connectors.base import BaseConnector
from app.core.data.connectors.clickhouse import (
    ClickHouseConnectionError,
    ClickHouseExtensionUnavailableError,
)
from app.core.data.connectors.csv import (
    CSVConnector,
    CSVLoadError,
    datasource_tables,
    table_name,
)
from app.core.data.connectors.factory import build_connector
from app.core.data.connectors.postgres import (
    PostgresConnectionError,
    PostgresExtensionUnavailableError,
    quote_identifier,
)
from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.profiling import TableProfile
from app.core.memory.profile_memory import ProfileMemory

logger = logging.getLogger(__name__)
router = APIRouter()

UPLOAD_CHUNK = 1 << 20
DESCRIPTION_MAX_BYTES = 1 << 20
CSV_DATASOURCE = re.compile(r"csv_[0-9a-f]+")


def uploads_dir() -> Path:
    return Path(settings.storage_dir, "uploads").resolve()


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


def too_large(name: str) -> HTTPException:
    return HTTPException(
        status_code=413, detail=f"Описание «{name}» больше {DESCRIPTION_MAX_BYTES >> 20} МБ."
    )


def read_description(upload: UploadFile | None) -> str | None:
    """Текст документа-описания (README, словарь колонок) или None."""
    if upload is None:
        return None
    data = upload.file.read(DESCRIPTION_MAX_BYTES + 1)
    if len(data) > DESCRIPTION_MAX_BYTES:
        raise too_large(upload.filename)
    return data.decode("utf-8-sig", errors="replace")


async def describe_tables(
    connector: BaseConnector,
    db: DuckDBManager,
    tables: list[str],
    document: str | None,
    job: ingest.DescribeJob,
) -> None:
    """Фоновый перенос описания в паспорт: сбой не затрагивает подключение."""
    try:
        await ingest.describe(connector, db, tables, document, job=job)
    except Exception:
        logger.exception("Описание таблиц %s не перенесено в паспорт", tables)
        with db.lock:
            ProfileMemory(db).fail_pending(job.run)


def briefs(
    connector: BaseConnector, db: DuckDBManager, profiles: dict[str, TableProfile]
) -> list[TableBrief]:
    """Сводки паспортов подключённых таблиц. Под db.lock, после ingest.mark_pending."""
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
    folder = uploads_dir() / datasource_id
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
        connector = CSVConnector(paths, datasource_id)
        with db.lock:
            profiles = ingest.attach(connector, db)
            tables = list(profiles)
            job = ingest.mark_pending(connector, db, tables, document)
            summaries = briefs(connector, db, profiles)
    except CSVLoadError as error:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(error)) from None
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    background.add_task(describe_tables, connector, db, tables, document, job)
    return DatasourceOut(datasource_id=datasource_id, kind="csv", tables=tables, profiles=summaries)


@router.post("/db", response_model=DatasourceOut)
def attach_db(
    payload: DatasourceCreate,
    db: Annotated[DuckDBManager, Depends(get_db)],
    background: BackgroundTasks,
) -> DatasourceOut:
    if payload.description and len(payload.description.encode()) > DESCRIPTION_MAX_BYTES:
        raise too_large("description")
    try:
        connector = build_connector(payload.kind, payload.dsn, payload.db_schema)
        with db.lock:
            profiles = ingest.attach(connector, db)
            tables = list(profiles)
            job = ingest.mark_pending(connector, db, tables, payload.description)
            summaries = briefs(connector, db, profiles)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    except (PostgresConnectionError, ClickHouseConnectionError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from None
    except (PostgresExtensionUnavailableError, ClickHouseExtensionUnavailableError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from None
    except duckdb.Error as error:
        # Сбой профиля: ingest.attach уже откатил подключение.
        raise HTTPException(
            status_code=422,
            detail=f"Не удалось прочитать таблицы источника: {connector.scrub(str(error))}",
        ) from None
    except NotImplementedError:
        raise HTTPException(
            status_code=501, detail=f"Коннектор «{payload.kind}» пока не реализован."
        ) from None
    restore.remember(connector, payload.description)
    background.add_task(describe_tables, connector, db, tables, payload.description, job)
    datasource_id = getattr(connector, "identity", f"{payload.kind}_{uuid4().hex[:10]}")
    return DatasourceOut(
        datasource_id=datasource_id, kind=payload.kind, tables=tables, profiles=summaries
    )


def legacy_files(folder: Path) -> list[str]:
    """Файлы источника, таблицы которых могли загрузиться без id в комментарии.

    Имя файла, которое есть и в другом источнике, пропускается: по комментарию
    «csv:<файл>» не понять, чья это таблица.
    """
    others = {
        path.name
        for path in folder.parent.glob("*/*.csv")
        if path.parent != folder and not path.name.endswith(".utf8.csv")
    }
    return sorted(
        path.name
        for path in folder.glob("*.csv")
        if not path.name.endswith(".utf8.csv") and path.name not in others
    )


def forget_csv(db: DuckDBManager, datasource_id: str) -> list[str] | None:
    """Удалить CSV-источник: таблицы, их паспорта и загруженные файлы.

    Возвращает удалённые таблицы; None — такого источника нет.
    """
    if not CSV_DATASOURCE.fullmatch(datasource_id):
        return None
    folder = uploads_dir() / datasource_id
    with db.lock:
        tables = datasource_tables(
            db, datasource_id, legacy_files(folder) if folder.is_dir() else []
        )
        if not tables and not folder.is_dir():
            return None
        memory = ProfileMemory(db)
        for table in tables:
            db.execute(f"DROP TABLE IF EXISTS {quote_identifier(table)}")
            memory.delete(table)
    shutil.rmtree(folder, ignore_errors=True)
    return tables


@router.delete("/{datasource_id}", status_code=204)
def detach(datasource_id: str, db: Annotated[DuckDBManager, Depends(get_db)]) -> None:
    """Удалить источник: БД отключить и забыть, CSV — удалить таблицы и файлы.

    После рестарта удалённый источник не возвращается.
    """
    if restore.forget(db, datasource_id) is not None:
        return
    if forget_csv(db, datasource_id) is None:
        raise HTTPException(status_code=404, detail=f"Источник «{datasource_id}» не найден.")
