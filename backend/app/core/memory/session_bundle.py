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
БД — по имени таблицы в той же базе; нашлась — сессия к ней привязывается. При
импорте ничего из бандла не выполняется — ни SQL шагов, ни LLM.

Рецепт (#250): у хода с графиком и у плитки дашборда в бандле — ``replay``,
откуда пересчитать график у получателя: шаг хода (``turn_id``, ``step_id``)
или обзор таблицы (``overview``). Шаг находится по таблице результата автора:
ход хранит результаты шагов по порядку (``results``). Нет ``replay`` —
график не пересчитывается и едет с данными автора: шаг не найден, график
построен не по результату шага (гистограмма распределения) или в цепочке
шагов есть Python. У таблицы БД в источнике — её имя в самой базе
(``source_table``): по нему она находится у получателя под любым именем.
Так же описана каждая таблица ходов (``origins``): сессия может идти по
нескольким таблицам, и у получателя ищется каждая (``check_tables``). У
старых ссылок ``origins`` нет — ищется только активная таблица.

Файл ``.session`` (#251) везёт итоговые таблицы ходов и плиток: в его бандле
ссылки на ``temp_result_vN`` остаются (``build(results=True)``), а импорт
переписывает их на новые имена у получателя (``load(tables=...)``). Ссылка на
таблицу, которой нет в словаре, обнуляется: чужие таблицы получателя импорт
не трогает.
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
from app.models.research_step import NOTE_LIMIT, TILE_STEP_IDS

FORMAT = 1
IMPORTED_SUFFIX = " (импорт)"
TITLE_LIMIT = 200
TEXT_LIMIT = 100_000
MAX_TURNS = 500
MAX_STEPS = 50
MAX_MEMORIES = 200
MAX_DASHBOARDS = 50
MAX_TILES = 50
MAX_TABLES = 50
KINDS = {"pg": "postgres", "ch": "clickhouse", "csv": "csv"}
CSV_DATASOURCE = re.compile(r"csv_[0-9a-f]+")
# Шаги с кодом Python: у получателя код автора не выполняется никогда.
PYTHON_TOOLS = frozenset({"run_python", "run_wasm_python"})

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
    result_table: Name | None = None


class Replay(BaseModel):
    """Откуда пересчитать график: шаг хода или обзор таблицы («что передо мной?»)."""

    turn_id: Name | None = None
    step_id: int | None = None
    overview: bool = False


class BundleTurn(BaseModel):
    turn_id: Name
    question: Text
    tables: list[Text] = Field(default_factory=list)
    steps: list[BundleStep] = Field(default_factory=list, max_length=MAX_STEPS)
    result_columns: list[Text] = Field(default_factory=list)
    headline: Text | None = None
    result_table: Name | None = None
    chart: BundleChart | None = None
    status: Literal["ok", "failed", "clarification"]
    reply_to: Name | None = None
    replay: Replay | None = None


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
    # У таблицы БД — её имя в самой базе: имя представления у получателя другое.
    source_table: Text | None = None
    origins: list["BundleSource"] = Field(default_factory=list, max_length=MAX_TABLES)


class BundleTile(DashboardTile):
    replay: Replay | None = None


class BundleDashboard(BaseModel):
    id: Name
    title: Text
    template_id: Name | None = None
    tiles: list[BundleTile] = Field(default_factory=list, max_length=MAX_TILES)


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
    db_schema: str | None = None
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


def build(session_id: str, db=None, results: bool = False) -> dict:
    """Бандл сохранённой сессии; ``KeyError`` — такой сессии нет.

    Собирается без проверки лимитов: их проверяет импорт. Без ``db`` источник
    берётся по ``datasource_id`` сессии, без таблицы и хэша. ``results`` —
    у ходов остаются итоговые таблицы (файл ``.session``).
    """
    memory = SessionMemory(session_id)
    if not memory.exists():
        raise KeyError(session_id)
    store = DashboardStore()
    dashboards = [store.get(summary.id) for summary in reversed(store.list(session_id))]
    turns = {turn["turn_id"]: turn for turn in memory.turns}
    sources = result_steps(memory.turns)
    replay_tiles = memory.replay_tiles
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
        "turns": [
            exported_turn(turn, turn_replay(turn, sources, turns), results) for turn in memory.turns
        ],
        "memories": [
            {key: item[key] for key in BundleMemory.model_fields}
            for item in memory.memories(include_disabled=True)
        ],
        "dashboards": [
            {
                **dashboard.model_dump(
                    mode="json", exclude={"session_id", "created_at", "updated_at", "tiles"}
                ),
                "tiles": [
                    {
                        **tile.model_dump(mode="json"),
                        "replay": dump(
                            checked(sources.get(tile.result_table or ""), turns)
                            or checked(replay_tiles.get(tile.id), turns)
                        ),
                    }
                    for tile in dashboard.tiles
                ],
            }
            for dashboard in dashboards
            if dashboard is not None
        ],
    }


def result_steps(turns: list[dict]) -> dict[str, Replay]:
    """Таблица результата автора -> шаг хода, который её построил.

    Результаты шагов записаны в ход по порядку шагов; ход, где их число не
    совпадает с числом шагов, пропускается.
    """
    found = {}
    for turn in turns:
        steps, results = turn.get("steps") or [], turn.get("results") or []
        if len(steps) != len(results):
            continue
        for step, table in zip(steps, results, strict=True):
            found[table] = Replay(turn_id=turn["turn_id"], step_id=step["step_id"])
    return found


def turn_replay(turn: dict, sources: dict[str, Replay], turns: dict[str, dict]) -> Replay | None:
    """Откуда пересчитать график хода; None — не пересчитывается.

    «Сделай таблицей» берёт шаг хода, график которого перестроил; у
    импортированного хода — ``replay`` из его бандла.
    """
    if turn.get("status") != "ok":
        return None
    if turn.get("overview"):
        return Replay(overview=True)
    if turn.get("replay"):
        return checked(Replay.model_validate(turn["replay"]), turns)
    chart = turn.get("chart") or {}
    return checked(sources.get(chart.get("result_table") or ""), turns)


def checked(replay: Replay | dict | None, turns: dict[str, dict]) -> Replay | None:
    """``replay``, если шаг есть и в его цепочке нет Python; иначе None."""
    if isinstance(replay, dict):
        replay = Replay.model_validate(replay)
    if replay is None or replay.overview:
        return replay
    turn = turns.get(replay.turn_id or "")
    steps = chain(turn.get("steps") or [], replay.step_id) if turn is not None else None
    if not steps or any(step.get("tool") in PYTHON_TOOLS for step in steps):
        return None
    return replay


def chain(steps: list[dict], step_id: int | None) -> list[dict] | None:
    """Шаги, которые выполняются до шага ``step_id`` включительно; None — шага нет.

    У дашборда по идее у каждой плитки своя сотня номеров и своя цепочка
    ``$prev``: шаги других плиток в цепочку не входят.
    """
    if step_id is None:
        return None
    group = step_id // TILE_STEP_IDS
    out = []
    for step in steps:
        if step.get("step_id", 0) // TILE_STEP_IDS != group:
            continue
        out.append(step)
        if step.get("step_id") == step_id:
            return out
    return None


def dump(replay: Replay | None) -> dict | None:
    return replay.model_dump() if replay is not None else None


def exported_turn(turn: dict, replay: Replay | None = None, results: bool = False) -> dict:
    """Ход для бандла: только поля BundleTurn; таблицы результата — только с ``results``.

    Пустые поля (у старых ходов их может не быть) не пишутся: импорт подставит
    значения по умолчанию. ``replay`` — откуда пересчитать график (рецепт).
    """
    chart = turn.get("chart")
    out = {
        **{key: turn[key] for key in BundleTurn.model_fields if turn.get(key) is not None},
        "replay": dump(replay),
        "steps": [
            {key: step[key] for key in BundleStep.model_fields if step.get(key) is not None}
            for step in turn.get("steps") or []
        ],
        "chart": None
        if chart is None
        else {"type": chart["type"], "title": chart.get("title", "")},
    }
    if not results:
        out.pop("result_table", None)
    elif chart is not None:
        out["chart"]["result_table"] = chart.get("result_table")
    return out


def describe_source(memory: SessionMemory, db=None) -> BundleSource:
    """Источник сессии без пароля: тип, маскированный DSN, схема, таблицы, файлы CSV.

    Источник узнаётся по активной таблице; не узнан — по ``datasource_id``
    сессии. В ``origins`` — каждая узнанная таблица ходов. У импортированной
    сессии — источник её автора.
    """
    if memory.imported:
        return BundleSource.model_validate(memory.source or {})
    tables = list(
        dict.fromkeys(table for turn in memory.turns for table in turn.get("tables") or [])
    )
    table = memory.table
    if table is not None and table not in tables:
        tables.append(table)
    found = [describe_table(db, name) for name in tables] if db is not None else []
    origins = [origin for origin in found if origin is not None]
    active = next((origin for origin in origins if origin.table == table), None)
    if active is not None:
        return active.model_copy(update={"tables": tables, "origins": origins})
    datasource_id = memory.datasource_id or ""
    record = DatasourceStore().get(datasource_id) if datasource_id else None
    kind = record.kind if record is not None else KINDS.get(datasource_id.split("_", 1)[0])
    return BundleSource(
        kind=kind,
        dsn=masked_dsn(record.kind, record.dsn) if record is not None else None,
        db_schema=record.db_schema if record is not None else None,
        tables=tables,
        files=csv_files(datasource_id),
        origins=origins,
    )


def describe_table(db, table: str) -> BundleSource | None:
    """Таблица без пароля: у CSV — число строк и хэш, у БД — адрес и имя в базе; None — не узнана."""
    found = table_origin.origin(db, table)
    if found is None:
        return None
    if found.kind == "csv":
        return BundleSource(
            kind="csv",
            files=csv_files(found.datasource_id or "") or [found.file],
            table=table,
            rows=table_origin.row_count(db, table),
            hash=table_origin.content_hash(db, table),
        )
    record = found.record
    return BundleSource(
        kind=record.kind,
        dsn=masked_dsn(record.kind, record.dsn),
        db_schema=record.db_schema,
        table=table,
        source_table=table_origin.view_target(db, table),
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
    check_format(raw.get("manifest") if isinstance(raw, dict) else None)
    try:
        return Bundle.model_validate(raw)
    except ValidationError as error:
        problem = error.errors()[0]
        where = ".".join(str(part) for part in problem["loc"])
        raise BundleFormatError(f"Сессия повреждена: {where} — {problem['msg']}.") from None


def check_format(manifest: Any) -> None:
    """Версия формата в манифесте — та, что открывает приложение; иначе BundleFormatError."""
    version = manifest.get("format") if isinstance(manifest, dict) else None
    if not isinstance(version, int) or isinstance(version, bool):
        raise BundleFormatError("Это не сессия: в данных нет версии формата.")
    if version != FORMAT:
        newer = " более новой версией приложения — обновите приложение" if version > FORMAT else ""
        raise BundleFormatError(
            f"Сессия сохранена в формате {version}{newer}. Это приложение открывает формат {FORMAT}."
        )


def load(raw: Any, db=None, tables: dict[str, str] | None = None) -> Imported:
    """Импортировать бандл в новую сессию; существующие сессии не меняются.

    С ``db`` таблица автора ищется у получателя, и сессия привязывается к ней.
    ``tables`` — таблица автора -> загруженная у получателя (файл ``.session``);
    ссылки на остальные таблицы обнуляются.
    """
    tables = tables or {}
    bundle = parse(raw)
    session_id = uuid.uuid4().hex
    title = f"{bundle.manifest.title or 'Сессия'}"[: TITLE_LIMIT - len(IMPORTED_SUFFIX)]
    title += IMPORTED_SUFFIX
    store = DashboardStore()
    dashboards = {}
    replay_tiles = {}
    for item in bundle.dashboards:
        tiles = [imported_tile(tile, tables) for tile in item.tiles]
        for source, tile in zip(item.tiles, tiles, strict=True):
            if source.replay is not None:
                replay_tiles[tile.id] = source.replay.model_dump()
        dashboard = Dashboard(
            session_id=session_id,
            title=item.title,
            template_id=item.template_id,
            tiles=tiles,
        )
        dashboards[item.id] = store.create(dashboard)
    memory = SessionMemory(session_id)
    memory.import_session(
        title,
        [imported_turn(turn, tables) for turn in bundle.turns],
        bundle.source.model_dump(),
        replay_tiles,
    )
    memory.import_memories([item.model_dump() for item in bundle.memories])
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


def check_tables(db, source: BundleSource) -> dict[str, TableCheck]:
    """Таблица автора -> нашлась ли она у получателя; у старых ссылок — только активная."""
    checks = (check_table(db, origin) for origin in source.origins or [source])
    return {check.table: check for check in checks if check is not None}


def check_table(db, source: BundleSource) -> TableCheck | None:
    """Найти у получателя таблицу автора; None — в бандле её нет.

    CSV — по хэшу содержимого под любым именем; таблица с тем же именем и
    другим хэшем — different. БД — представление над той же таблицей базы
    (``source_table``, у старых ссылок — имя представления автора) в
    подключённой базе с тем же хостом, портом, базой и схемой.
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
    check = TableCheck(
        status="not_connected",
        kind=source.kind,
        table=table,
        dsn=source.dsn,
        db_schema=source.db_schema,
    )
    identities = [
        identity
        for identity in table_origin.same_database(source.kind, source.dsn, source.db_schema)
        if table_origin.connected(db, identity)
    ]
    if not identities:
        return check
    bound = table_origin.find_view(db, identities, source.source_table or table, prefer=table)
    if bound is not None:
        return check.model_copy(update={"status": "found", "bound": bound})
    return check.model_copy(update={"status": "no_table"})


def renamed(table: Any, tables: dict[str, str]) -> str | None:
    """Имя таблицы автора у получателя; нет в словаре — None."""
    return tables.get(table) if isinstance(table, str) else None


def imported_turn(turn: BundleTurn, tables: dict[str, str] | None = None) -> dict:
    """Ход из бандла в состоянии сессии: помечен imported, таблицы — по словарю ``tables``."""
    tables = tables or {}
    chart = turn.chart.model_dump() if turn.chart is not None else None
    if chart is not None:
        chart["result_table"] = renamed(chart["result_table"], tables)
    return {
        **turn.model_dump(),
        "chart": chart,
        "result_table": renamed(turn.result_table, tables),
        "imported": True,
    }


def imported_tile(tile: BundleTile, tables: dict[str, str] | None = None) -> DashboardTile:
    """Плитка с новым id; таблица результата — по словарю ``tables``, нет — по спеке и снимку."""
    tables = tables or {}
    spec = dict(tile.spec)
    if isinstance(spec.get("usermeta"), dict):
        meta = spec["usermeta"]
        spec["usermeta"] = {**meta, "result_table": renamed(meta.get("result_table"), tables)}
    return DashboardTile(
        **tile.model_dump(exclude={"id", "result_table", "spec", "replay"}),
        id=uuid.uuid4().hex[:12],
        result_table=renamed(tile.result_table, tables),
        spec=spec,
    )
