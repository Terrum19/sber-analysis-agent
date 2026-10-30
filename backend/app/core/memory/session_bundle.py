"""Бандл сессии: всё, чтобы передать сессию другому пользователю (#249).

``build`` собирает из SQLite манифест (версия формата и приложения, id,
название, даты), ходы, воспоминания, дашборды сессии и описание источника:
``kind``, маскированный DSN, схему, таблицы; у CSV — имена файлов. Пароля в
бандле нет никогда: DSN заново маскируется при сборке. Ленту (текст ответов
и Vega-спеки) добавляет фронт — она лежит в браузере; бэкенд её не читает.

В источнике — активная таблица автора (``table``). Источник узнаётся по
самой таблице (``table_origin``): у CSV в бандл идут число строк и хэш
содержимого, у PostgreSQL и ClickHouse — адрес базы без пароля и схема.

``load`` создаёт из бандла новую сессию «… (импорт)» и существующие не
трогает. Сначала проверяется версия формата: неизвестная — ``BundleFormatError``
с понятным текстом. Ходы помечаются ``imported``, воспоминания приходят
выключенными, дашборды и плитки получают новые id. Ссылки на ``temp_result_vN``
обнуляются: у получателя под тем же номером лежат другие данные. Таблица
автора ищется у получателя (``check_table``): CSV — по хэшу под любым именем,
БД — по имени в той же базе; нашлась — сессия к ней привязывается. При
импорте ничего из бандла не выполняется — ни SQL шагов, ни LLM.
"""

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from app import __version__
from app.config import settings
from app.core.dashboards.models import Dashboard, DashboardTile
from app.core.dashboards.store import DashboardStore
from app.core.data import table_origin
from app.core.data.connectors import clickhouse, pg_dsn
from app.core.memory.datasource_store import DatasourceStore
from app.core.memory.session_memory import SessionMemory
from app.models.research_step import NOTE_LIMIT

FORMAT = 1
IMPORTED_SUFFIX = " (импорт)"
TITLE_LIMIT = 200
TEXT_LIMIT = 100_000
MAX_TURNS = 500
MAX_STEPS = 50
MAX_MEMORIES = 200
MAX_DASHBOARDS = 50
MAX_TILES = 50
KINDS = {"pg": "postgres", "ch": "clickhouse", "csv": "csv"}
CSV_DATASOURCE = re.compile(r"csv_[0-9a-f]+")

Text = Annotated[str, StringConstraints(max_length=TEXT_LIMIT)]
Name = Annotated[str, StringConstraints(max_length=TITLE_LIMIT)]


class BundleFormatError(ValueError):
    """Это не бандл сессии или бандл другой версии формата."""


class Manifest(BaseModel):
    format: int
    app_version: Name | None = None
    session_id: Name
    title: Name | None = None
    created_at: Name | None = None
    exported_at: Name | None = None


class BundleStep(BaseModel):
    step_id: int = 0
    tool: Name | None = None
    input: Text = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    columns: list[Any] = Field(default_factory=list)
    purpose: Text = ""


class BundleChart(BaseModel):
    type: Name
    title: Text = ""


class BundleTurn(BaseModel):
    turn_id: Name
    question: Text
    tables: list[Text] = Field(default_factory=list)
    steps: list[BundleStep] = Field(default_factory=list, max_length=MAX_STEPS)
    result_columns: list[Text] = Field(default_factory=list)
    headline: Text | None = None
    chart: BundleChart | None = None
    status: Literal["ok", "failed", "clarification"]
    reply_to: Name | None = None


class BundleMemory(BaseModel):
    text: Annotated[str, StringConstraints(max_length=NOTE_LIMIT)]
    source: Name = "user"
    created_at: Name
    turn_id: Name | None = None


class BundleSource(BaseModel):
    kind: Name | None = None
    dsn: Text | None = None
    db_schema: Text | None = None
    tables: list[Text] = Field(default_factory=list)
    files: list[Text] = Field(default_factory=list)
    # Активная таблица автора; у CSV — её число строк и хэш содержимого.
    table: Text | None = None
    rows: int | None = None
    hash: Name | None = None


class BundleDashboard(BaseModel):
    id: Name
    title: Text
    template_id: Name | None = None
    tiles: list[DashboardTile] = Field(default_factory=list, max_length=MAX_TILES)


class Bundle(BaseModel):
    """Бандл без ленты: ленту (``feed``) добавляет и разбирает фронт."""

    model_config = ConfigDict(extra="ignore")

    manifest: Manifest
    source: BundleSource = Field(default_factory=BundleSource)
    turns: list[BundleTurn] = Field(default_factory=list, max_length=MAX_TURNS)
    memories: list[BundleMemory] = Field(default_factory=list, max_length=MAX_MEMORIES)
    dashboards: list[BundleDashboard] = Field(default_factory=list, max_length=MAX_DASHBOARDS)


class TableCheck(BaseModel):
    """Нашлась ли у получателя таблица автора.

    found — нашлась, сессия к ней привязана (``bound``); different — CSV-таблица
    с тем же именем есть, но данные другие; missing — CSV-таблицы нет;
    no_table — та же база подключена, а таблицы в ней нет; not_connected —
    базы автора у получателя нет.
    """

    status: Literal["found", "different", "missing", "no_table", "not_connected"]
    kind: str
    table: str
    bound: str | None = None
    dsn: str | None = None
    file: str | None = None


class Imported(BaseModel):
    session_id: str
    title: str
    # Старый id дашборда -> новый: по нему фронт переписывает карточки ленты.
    dashboards: dict[str, str]
    # Таблица получателя, к которой привязана сессия; None — не нашлась.
    table: str | None = None
    # None — в бандле нет таблицы автора (старая ссылка или источник не узнан).
    check: TableCheck | None = None


def build(session_id: str, db=None) -> dict:
    """Бандл сохранённой сессии; ``KeyError`` — такой сессии нет.

    Собирается без проверки лимитов: их проверяет импорт. Без ``db`` источник
    берётся по ``datasource_id`` сессии, без таблицы и хэша.
    """
    memory = SessionMemory(session_id)
    if not memory.exists():
        raise KeyError(session_id)
    store = DashboardStore()
    dashboards = [store.get(summary.id) for summary in reversed(store.list(session_id))]
    return {
        "manifest": {
            "format": FORMAT,
            "app_version": __version__,
            "session_id": session_id,
            "title": memory.title,
            "created_at": memory.created_at,
            "exported_at": datetime.now(UTC).isoformat(),
        },
        "source": describe_source(memory, db).model_dump(),
        "turns": [exported_turn(turn) for turn in memory.turns],
        "memories": [
            {key: item[key] for key in BundleMemory.model_fields}
            for item in memory.memories(include_disabled=True)
        ],
        "dashboards": [
            dashboard.model_dump(mode="json", exclude={"session_id", "created_at", "updated_at"})
            for dashboard in dashboards
            if dashboard is not None
        ],
    }


def exported_turn(turn: dict) -> dict:
    """Ход для бандла: только поля BundleTurn, у графика — без таблицы результата.

    Пустые поля (у старых ходов их может не быть) не пишутся: импорт подставит
    значения по умолчанию.
    """
    chart = turn.get("chart")
    return {
        **{key: turn[key] for key in BundleTurn.model_fields if turn.get(key) is not None},
        "steps": [
            {key: step[key] for key in BundleStep.model_fields if step.get(key) is not None}
            for step in turn.get("steps") or []
        ],
        "chart": None
        if chart is None
        else {"type": chart["type"], "title": chart.get("title", "")},
    }


def describe_source(memory: SessionMemory, db=None) -> BundleSource:
    """Источник сессии без пароля: тип, маскированный DSN, схема, таблицы, файлы CSV.

    Источник узнаётся по активной таблице; не узнан — по ``datasource_id``
    сессии. У импортированной сессии — источник её автора.
    """
    if memory.imported:
        return BundleSource.model_validate(memory.source or {})
    tables = list(
        dict.fromkeys(table for turn in memory.turns for table in turn.get("tables") or [])
    )
    table = memory.table
    if table is not None and table not in tables:
        tables.append(table)
    found = table_origin.origin(db, table) if db is not None and table is not None else None
    if found is not None and found.kind == "csv":
        return BundleSource(
            kind="csv",
            tables=tables,
            files=csv_files(found.datasource_id or "") or [found.file],
            table=table,
            rows=table_origin.row_count(db, table),
            hash=table_origin.content_hash(db, table),
        )
    if found is not None:
        record = found.record
        return BundleSource(
            kind=record.kind,
            dsn=masked_dsn(record.kind, record.dsn),
            db_schema=record.db_schema,
            tables=tables,
            table=table,
        )
    datasource_id = memory.datasource_id or ""
    record = DatasourceStore().get(datasource_id) if datasource_id else None
    kind = record.kind if record is not None else KINDS.get(datasource_id.split("_", 1)[0])
    return BundleSource(
        kind=kind,
        dsn=masked_dsn(record.kind, record.dsn) if record is not None else None,
        db_schema=record.db_schema if record is not None else None,
        tables=tables,
        files=csv_files(datasource_id),
    )


def masked_dsn(kind: str, dsn: str) -> str | None:
    """DSN, замаскированный заново: даже строка с паролем уходит без него."""
    try:
        if kind == "postgres":
            return pg_dsn.parse_dsn(dsn).masked()
        if kind == "clickhouse":
            return clickhouse.parse_dsn(dsn).masked()
    except ValueError:
        return None
    return None


def csv_files(datasource_id: str) -> list[str]:
    """Имена загруженных файлов CSV-источника; не CSV-источник — пусто."""
    if not CSV_DATASOURCE.fullmatch(datasource_id):
        return []
    folder = Path(settings.storage_dir, "uploads", datasource_id)
    return sorted(path.name for path in folder.glob("*.csv") if not path.name.endswith(".utf8.csv"))


def parse(raw: Any) -> Bundle:
    """Проверить бандл: сначала версию формата, потом остальное."""
    manifest = raw.get("manifest") if isinstance(raw, dict) else None
    version = manifest.get("format") if isinstance(manifest, dict) else None
    if not isinstance(version, int) or isinstance(version, bool):
        raise BundleFormatError("Это не сессия: в данных нет версии формата.")
    if version != FORMAT:
        newer = " более новой версией приложения — обновите приложение" if version > FORMAT else ""
        raise BundleFormatError(
            f"Сессия сохранена в формате {version}{newer}. Это приложение открывает формат {FORMAT}."
        )
    try:
        return Bundle.model_validate(raw)
    except ValidationError as error:
        problem = error.errors()[0]
        where = ".".join(str(part) for part in problem["loc"])
        raise BundleFormatError(f"Сессия повреждена: {where} — {problem['msg']}.") from None


def load(raw: Any, db=None) -> Imported:
    """Импортировать бандл в новую сессию; существующие сессии не меняются.

    С ``db`` таблица автора ищется у получателя, и сессия привязывается к ней.
    """
    bundle = parse(raw)
    session_id = uuid.uuid4().hex
    title = f"{bundle.manifest.title or 'Сессия'}"[: TITLE_LIMIT - len(IMPORTED_SUFFIX)]
    title += IMPORTED_SUFFIX
    memory = SessionMemory(session_id)
    memory.import_session(
        title,
        [imported_turn(turn) for turn in bundle.turns],
        bundle.source.model_dump(),
    )
    memory.import_memories([item.model_dump() for item in bundle.memories])
    store = DashboardStore()
    dashboards = {}
    for item in bundle.dashboards:
        dashboard = Dashboard(
            session_id=session_id,
            title=item.title,
            template_id=item.template_id,
            tiles=[imported_tile(tile) for tile in item.tiles],
        )
        dashboards[item.id] = store.create(dashboard)
    check = check_table(db, bundle.source) if db is not None else None
    if check is not None and check.bound is not None:
        memory.set_table(check.bound)
    return Imported(
        session_id=session_id,
        title=title,
        dashboards=dashboards,
        table=check.bound if check is not None else None,
        check=check,
    )


def check_table(db, source: BundleSource) -> TableCheck | None:
    """Найти у получателя таблицу автора; None — в бандле её нет.

    CSV — по хэшу содержимого под любым именем; таблица с тем же именем и
    другим хэшем — different. БД — таблица с тем же именем в подключённой
    базе с тем же хостом, портом, базой и схемой.
    """
    table = source.table
    if table is None or source.kind is None:
        return None
    if source.kind == "csv":
        if source.rows is None or source.hash is None:
            return None
        file = source.files[0] if source.files else None
        bound = table_origin.find_csv(db, source.rows, source.hash, prefer=table)
        if bound is not None:
            return TableCheck(status="found", kind="csv", table=table, bound=bound, file=file)
        status = "different" if db.columns(table) else "missing"
        return TableCheck(status=status, kind="csv", table=table, file=file)
    if source.dsn is None:
        return None
    check = TableCheck(status="not_connected", kind=source.kind, table=table, dsn=source.dsn)
    identities = [
        identity
        for identity in table_origin.same_database(source.kind, source.dsn, source.db_schema)
        if table_origin.connected(db, identity)
    ]
    if not identities:
        return check
    if table_origin.view_identity(db, table) in identities:
        return check.model_copy(update={"status": "found", "bound": table})
    return check.model_copy(update={"status": "no_table"})


def imported_turn(turn: BundleTurn) -> dict:
    """Ход из бандла в состоянии сессии: помечен imported, без таблиц результата."""
    chart = turn.chart.model_dump() if turn.chart is not None else None
    if chart is not None:
        chart["result_table"] = None
    return {
        **turn.model_dump(),
        "chart": chart,
        "result_table": None,
        "imported": True,
    }


def imported_tile(tile: DashboardTile) -> DashboardTile:
    """Плитка с новым id и без таблицы результата: рисуется по спеке и снимку."""
    spec = dict(tile.spec)
    if isinstance(spec.get("usermeta"), dict):
        spec["usermeta"] = {**spec["usermeta"], "result_table": None}
    return tile.model_copy(update={"id": uuid.uuid4().hex[:12], "result_table": None, "spec": spec})
