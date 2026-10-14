"""Подключение источников данных: CSV / PostgreSQL / ClickHouse.

Ключевое требование: подключение БЕЗ предобработки — сырые данные
грузятся в DuckDB, вся трансформация внутри платформы (см. core/data/).
"""
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from app.api.deps import get_db
from app.api.schemas.datasource import DatasourceCreate, DatasourceOut
from app.core.data.connectors.factory import build_connector
from app.core.data.connectors.postgres import (
    PostgresConnectionError,
    PostgresExtensionUnavailableError,
)
from app.core.data.duckdb_manager import DuckDBManager

router = APIRouter()


@router.post("/csv", response_model=DatasourceOut)
async def attach_csv(file: UploadFile) -> DatasourceOut:
    """Загрузить сырой CSV во временный слой DuckDB.

    TODO(round-2): сохранить файл в storage/, вызвать CSVConnector,
    вернуть datasource_id + первичный «паспорт» данных (профилирование).
    """
    raise NotImplementedError


@router.post("/db", response_model=DatasourceOut)
def attach_db(
    payload: DatasourceCreate, db: Annotated[DuckDBManager, Depends(get_db)]
) -> DatasourceOut:
    try:
        connector = build_connector(payload.kind, payload.dsn, payload.schema)
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
