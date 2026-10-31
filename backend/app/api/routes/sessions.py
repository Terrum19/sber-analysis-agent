"""CRUD сессий диалога.

Сессия — единица памяти (память уровня 1): session_id -> ходы диалога
(см. core/memory/session_memory.py). Таблица при создании необязательна;
PATCH меняет таблицу по умолчанию и/или название, ходы при этом остаются.
Воспоминания сессии (#197) — ``/{id}/memories``: список, добавление,
включение и выключение записи. ``/{id}/dashboard-ideas`` — варианты
дашборда по таблице сессии для кнопок в чате (#224).
``/{id}/results/{table}`` — страница результата шага хода этой сессии (#239).
``/{id}/bundle`` и ``/import`` — бандл сессии и импорт его в новую сессию
(#249); ``/{id}/replay`` — пересчёт графиков импортированной сессии по рецепту
на данных получателя (#250); ``/{id}/export-file`` и ``/import-file`` — файл
``.session`` с таблицами (#251).
Источник правды — SQLite SessionMemory: сессии и их метаданные переживают
перезапуск.
"""

import asyncio
import json
import logging
import os
import pathlib
import tempfile
import uuid
from typing import Annotated

import duckdb
from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.api.deps import get_db
from app.api.schemas.chat import (
    DashboardIdeasOut,
    DashboardIdeasRequest,
    DashboardPayload,
    MemoryCreate,
    MemoryOut,
    MemoryUpdate,
    SessionCreate,
    SessionFileRequest,
    SessionOut,
    SessionUpdate,
    StepResultOut,
)
from app.core.agent.replay import Replayer, ReplayOut, ReplayRequest
from app.core.dashboards.ideas import generate_ideas
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory import session_bundle, session_file
from app.core.memory.session_memory import SessionMemory

logger = logging.getLogger(__name__)

router = APIRouter()

SESSION_LIST_LIMIT = 30
BUNDLE_MAX_BYTES = 10 << 20
RESULT_PAGE_LIMIT = 500
# Имя попадает в SQL как таблица — пускаем только temp_result_vN.
ResultTable = Annotated[str, Path(pattern=r"^temp_result_v\d+$")]


def table_columns(db: DuckDBManager, table: str) -> list[str]:
    """Колонки подключённой таблицы; 404, если такой таблицы нет."""
    columns = db.columns(table)
    if not columns:
        raise HTTPException(status_code=404, detail=f"Таблица «{table}» не найдена.")
    return columns


def existing(session_id: str) -> SessionMemory:
    """Сохранённая сессия; 404, если её нет."""
    memory = SessionMemory(session_id)
    if not memory.exists():
        raise HTTPException(status_code=404, detail="Сессия не найдена.")
    return memory


def session_out(memory: SessionMemory) -> SessionOut:
    return SessionOut(
        session_id=memory.session_id,
        table=memory.table,
        datasource_id=memory.datasource_id,
        title=memory.title,
        created_at=memory.created_at,
        imported=memory.imported,
    )


@router.get("", response_model=list[SessionOut])
def list_sessions(offset: Annotated[int, Query(ge=0)] = 0) -> list[SessionOut]:
    """Сессии с ходами для сайдбара, недавно изменённые первыми, по SESSION_LIST_LIMIT с offset."""
    rows = SessionMemory.list_sessions(SESSION_LIST_LIMIT, offset)
    return [SessionOut(**row) for row in rows]


@router.post("", response_model=SessionOut)
def create_session(
    payload: SessionCreate, db: Annotated[DuckDBManager, Depends(get_db)]
) -> SessionOut:
    """Создать новую сессию диалога; таблица, если задана, становится таблицей по умолчанию."""
    if payload.table is not None:
        table_columns(db, payload.table)
    memory = SessionMemory(uuid.uuid4().hex, db)
    memory.set_meta(payload.title, payload.datasource_id)
    if payload.table is not None:
        memory.set_table(payload.table)
    return session_out(memory)


@router.post("/import", response_model=session_bundle.Imported)
async def import_session(
    request: Request, db: Annotated[DuckDBManager, Depends(get_db)]
) -> session_bundle.Imported:
    """Создать новую сессию из бандла; существующие сессии не меняются.

    Неизвестная версия формата и повреждённый бандл — 400 с текстом для
    пользователя, бандл больше BUNDLE_MAX_BYTES — 413. Ничего из бандла не
    выполняется; таблица автора ищется в каталоге получателя, нашлась — сессия
    привязывается к ней.
    """
    body = await request.body()
    if len(body) > BUNDLE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Сессия слишком большая для импорта.")
    try:
        raw = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Это не сессия: данные не читаются.") from None
    try:
        return await asyncio.to_thread(session_bundle.load, raw, db)
    except session_bundle.BundleFormatError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


def file_error(error: session_file.SessionFileError | session_bundle.BundleFormatError):
    """413 — файл больше лимита, 400 — остальное, с текстом для пользователя."""
    status = 413 if isinstance(error, session_file.SessionFileTooLarge) else 400
    return HTTPException(status_code=status, detail=str(error))


@router.post("/import-file", response_model=session_file.ImportedFile)
async def import_session_file(
    file: Annotated[UploadFile, File()], db: Annotated[DuckDBManager, Depends(get_db)]
) -> session_file.ImportedFile:
    """Создать новую сессию из файла .session (#251); существующие сессии и таблицы не меняются.

    Таблицы файла загружаются в новые temp_result_vN. Больше лимита — 413, не
    файл сессии, лишнее внутри zip или неизвестная версия формата — 400.
    """
    with tempfile.TemporaryDirectory() as folder:
        path = pathlib.Path(folder, "upload.session")
        try:
            await asyncio.to_thread(session_file.receive, file.file, path)
            return await asyncio.to_thread(session_file.read, path, db)
        except (session_file.SessionFileError, session_bundle.BundleFormatError) as error:
            raise file_error(error) from None


@router.patch("/{session_id}", response_model=SessionOut)
def update_session(
    session_id: str, payload: SessionUpdate, db: Annotated[DuckDBManager, Depends(get_db)]
) -> SessionOut:
    """Сменить таблицу по умолчанию и/или переименовать сессию.

    Неизвестная таблица — 404 до любых изменений: название тоже не меняется.
    """
    memory = SessionMemory(session_id, db)
    if not memory.exists():
        raise HTTPException(status_code=404, detail="Сессия не найдена.")
    if payload.table is not None:
        table_columns(db, payload.table)
        memory.set_table(payload.table)
    if payload.title is not None:
        memory.set_title(payload.title)
    return session_out(memory)


@router.get("/{session_id}", response_model=SessionOut)
def get_session(session_id: str) -> SessionOut:
    """Получить метаданные сессии."""
    return session_out(existing(session_id))


@router.get("/{session_id}/bundle")
def get_bundle(session_id: str, db: Annotated[DuckDBManager, Depends(get_db)]) -> dict:
    """Бандл сессии без ленты: ленту добавляет фронт (#249). Пароля в нём нет.

    У CSV-таблицы считается хэш содержимого: один проход по таблице.
    """
    try:
        return session_bundle.build(session_id, db)
    except KeyError:
        raise HTTPException(status_code=404, detail="Сессия не найдена.") from None


@router.post("/{session_id}/export-file")
async def export_session_file(
    session_id: str, payload: SessionFileRequest, db: Annotated[DuckDBManager, Depends(get_db)]
) -> FileResponse:
    """Файл .session (#251): бандл, лента из тела и итоговые таблицы в parquet.

    Пароля в нём нет. Больше лимита импорта — 413: получатель его не откроет.
    """
    handle, name = tempfile.mkstemp(suffix=".session")
    os.close(handle)
    path = pathlib.Path(name)
    try:
        await asyncio.to_thread(
            session_file.write, session_id, db, payload.table, payload.feed, path
        )
    except BaseException as error:
        path.unlink(missing_ok=True)
        if isinstance(error, KeyError):
            raise HTTPException(status_code=404, detail="Сессия не найдена.") from None
        if isinstance(error, session_file.SessionFileError):
            raise file_error(error) from None
        raise
    return FileResponse(
        path,
        media_type="application/zip",
        filename="session.session",
        background=BackgroundTask(path.unlink),
    )


@router.post("/{session_id}/replay", response_model=ReplayOut)
async def replay_session(
    session_id: str, payload: ReplayRequest, db: Annotated[DuckDBManager, Depends(get_db)]
) -> ReplayOut:
    """Пересчитать графики и плитки импортированной сессии на данных получателя (#250).

    Таблицы автора ищутся заново (после подключения базы они могли появиться);
    не нашлась ни одна — ответ с ``check`` активной таблицы и без графиков.
    Иначе шаги автора выполняются без LLM на найденных таблицах, а ход, где
    какой-то таблицы нет, остаётся с причиной в ``failed``. Нашлась активная
    таблица — сессия к ней привязывается. Python-шаги не выполняются.
    Не импортированная сессия — 400.
    """
    memory = existing(session_id)
    if not memory.imported:
        raise HTTPException(
            status_code=400, detail="Пересчитать можно только импортированную сессию."
        )
    source = session_bundle.BundleSource.model_validate(memory.source or {})
    checks = await asyncio.to_thread(session_bundle.check_tables, db, source)
    check = checks.get(source.table or "")
    if not any(item.bound for item in checks.values()):
        return ReplayOut(check=check)
    memory = SessionMemory(session_id, db)
    if check is not None and check.bound is not None:
        memory.set_table(check.bound)
    out, dashboards = await Replayer(memory, db, checks).run(payload.charts)
    out.check = check
    out.dashboards = [
        DashboardPayload.from_dashboard(dashboard).model_dump(exclude_none=True)
        for dashboard in dashboards
    ]
    return out


@router.get("/{session_id}/memories", response_model=list[MemoryOut])
def list_memories(session_id: str) -> list[MemoryOut]:
    """Все воспоминания сессии, включённые и выключенные, старые первыми."""
    return [MemoryOut(**item) for item in existing(session_id).memories(include_disabled=True)]


@router.post("/{session_id}/memories", response_model=MemoryOut, status_code=201)
def add_memory(session_id: str, payload: MemoryCreate) -> MemoryOut:
    """Добавить воспоминание; тот же текст не дублируется, а снова включается."""
    return MemoryOut(**existing(session_id).remember(payload.text))


@router.patch("/{session_id}/memories/{memory_id}", response_model=MemoryOut)
def update_memory(session_id: str, memory_id: str, payload: MemoryUpdate) -> MemoryOut:
    """Включить или выключить воспоминание: выключенное не идёт в контекст хода."""
    item = existing(session_id).set_memory_enabled(memory_id, payload.enabled)
    if item is None:
        raise HTTPException(status_code=404, detail="Воспоминание не найдено.")
    return MemoryOut(**item)


@router.post("/{session_id}/dashboard-ideas", response_model=DashboardIdeasOut)
def dashboard_ideas(
    session_id: str,
    db: Annotated[DuckDBManager, Depends(get_db)],
    body: DashboardIdeasRequest | None = None,
) -> DashboardIdeasOut:
    """4 варианта дашборда по таблице сессии; генерируются только по этому запросу.

    Тело с exclude — новые варианты, не повторяющие перечисленные.

    Синхронный обработчик идёт в пуле потоков: DuckDB и LLM не держат цикл сервера.
    """
    table = existing(session_id).table
    if table is None:
        raise HTTPException(status_code=409, detail="К сессии не подключена таблица.")
    try:
        ideas = asyncio.run(generate_ideas(db, table, exclude=body.exclude if body else ()))
    except Exception as exc:
        logger.exception("идеи дашборда для %s не построены", table)
        raise HTTPException(status_code=502, detail=f"Модель не ответила: {exc}") from exc
    return DashboardIdeasOut(ideas=ideas)


@router.delete("/{session_id}")
def delete_session(session_id: str) -> dict[str, bool]:
    """Удалить сессию диалога."""
    deleted = SessionMemory.delete(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Сессия не найдена.")
    return {"ok": True}


@router.get("/{session_id}/results/{table}", response_model=StepResultOut)
def read_result(
    session_id: str,
    table: ResultTable,
    db: Annotated[DuckDBManager, Depends(get_db)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=RESULT_PAGE_LIMIT)] = 50,
) -> StepResultOut:
    """Страница результата шага; чужой или удалённый результат — 404."""
    if not existing(session_id).owns_result(table):
        raise HTTPException(status_code=404, detail="Результат не найден.")
    try:
        columns, total = db.result_shape(table)
        rows = db.to_records(table, limit=limit, offset=offset) if columns else []
    except duckdb.Error:
        logger.exception("результат %s не прочитан", table)
        columns = []
    if not columns:
        raise HTTPException(status_code=404, detail="Результат больше недоступен.")
    return StepResultOut(columns=columns, rows=rows, total=total)
