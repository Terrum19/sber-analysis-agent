"""Подключение источников данных: CSV / PostgreSQL / ClickHouse.

Ключевое требование: подключение БЕЗ предобработки — сырые данные
грузятся в DuckDB, вся трансформация внутри платформы (см. core/data/).
"""
from fastapi import APIRouter, UploadFile

from app.api.schemas.datasource import DatasourceCreate, DatasourceOut

router = APIRouter()


@router.post("/csv", response_model=DatasourceOut)
async def attach_csv(file: UploadFile) -> DatasourceOut:
    """Загрузить сырой CSV во временный слой DuckDB.

    TODO(round-2): сохранить файл в storage/, вызвать CSVConnector,
    вернуть datasource_id + первичный «паспорт» данных (профилирование).
    """
    raise NotImplementedError


@router.post("/db", response_model=DatasourceOut)
async def attach_db(payload: DatasourceCreate) -> DatasourceOut:
    """Подключить PostgreSQL/ClickHouse через DuckDB-коннектор.

    TODO(round-2): выбрать коннектор по payload.kind, создать VIEW/ATTACH
    в DuckDB под единым диалектом SQL, вернуть datasource_id.
    """
    raise NotImplementedError
