"""Бандл сессии: всё, чтобы передать сессию другому пользователю (#249).

``build`` собирает из SQLite манифест (версия формата и приложения, id,
название, даты), ходы, воспоминания, дашборды сессии и описание источника:
``kind``, маскированный DSN, схему, таблицы; у CSV — имена файлов. Пароля в
бандле нет никогда: DSN заново маскируется при сборке. Ленту (текст ответов
и Vega-спеки) добавляет фронт — она лежит в браузере; бэкенд её не читает.

``load`` создаёт из бандла новую сессию «… (импорт)» и существующие не
трогает. Сначала проверяется версия формата: неизвестная — ``BundleFormatError``
с понятным текстом. Ходы помечаются ``imported``, воспоминания приходят
выключенными, дашборды и плитки получают новые id. Ссылки на ``temp_result_vN``
обнуляются: у получателя под тем же номером лежат другие данные. При импорте
ничего не выполняется — ни SQL, ни LLM.
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


class Imported(BaseModel):
    session_id: str
    title: str
    # Старый id дашборда -> новый: по нему фронт переписывает карточки ленты.
    dashboards: dict[str, str]


def build(session_id: str) -> dict:
    """Бандл сохранённой сессии; ``KeyError`` — такой сессии нет.

    Собирается без проверки лимитов: их проверяет импорт.
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
        "source": describe_source(memory).model_dump(),
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


def describe_source(memory: SessionMemory) -> BundleSource:
    """Источник сессии без пароля: тип, маскированный DSN, схема, таблицы, файлы CSV.

    У импортированной сессии — источник её автора.
    """
    if memory.imported:
        return BundleSource.model_validate(memory.source or {})
    tables = list(
        dict.fromkeys(table for turn in memory.turns for table in turn.get("tables") or [])
    )
    if memory.table is not None and memory.table not in tables:
        tables.append(memory.table)
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


def load(raw: Any) -> Imported:
    """Импортировать бандл в новую сессию; существующие сессии не меняются."""
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
    return Imported(session_id=session_id, title=title, dashboards=dashboards)


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
