"""Пересчёт импортированной сессии у получателя по рецепту (#250).

Получатель с той же таблицей (CSV с тем же хэшем или та же база) нажимает
«Пересчитать»: шаги автора выполняются на его данных тем же
``StepExecutor``, а графики строятся тем же типом и с теми же подписями и
порядком колонок, что у автора. Ни планировщик, ни инсайт, ни исправление
шагов не вызываются: записаны уже итоговые шаги, текст ответа остаётся
авторским.

Откуда пересчитать график, записано в бандле (``replay``): шаг хода или обзор
таблицы. Шаги идут цепочкой ``$prev`` до нужного шага (``session_bundle.chain``);
выполненный шаг повторно не считается, даже если на него опираются несколько
графиков. Каждая таблица автора заменяется таблицей, найденной у получателя
(``session_bundle.check_tables``). Ход, где какой-то таблицы у получателя
нет, не пересчитывается: в ``failed`` — какая не нашлась. Шаг с кодом Python не
выполняется никогда. Упавший шаг не прерывает пересчёт: ход или плитка
остаются с причиной в ``failed``.
"""

import logging
import time
from typing import Any

from pydantic import BaseModel, Field

from app.core.agent.orchestrator import OVERVIEW_CAPTION
from app.core.agent.step_executor import StepExecutor, tool_kind
from app.core.dashboards.models import Dashboard
from app.core.dashboards.recalculate import rebuild_spec
from app.core.dashboards.store import DashboardStore, snapshot_records
from app.core.data import overview
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory.profile_memory import ProfileMemory
from app.core.memory.session_bundle import (
    PYTHON_TOOLS,
    BundleSource,
    Replay,
    TableCheck,
    chain,
    check_tables,
)
from app.core.memory.session_memory import SessionMemory
from app.core.viz.vega_spec_builder import SPEC_ROW_LIMIT, build_vega_spec
from app.models.research_step import ResearchStep

logger = logging.getLogger(__name__)


class ReplayError(Exception):
    """График не пересчитать; текст — причина для пользователя."""


class ReplayRequest(BaseModel):
    # Спеки графиков ленты по turn_id (без точек): тип, подписи и порядок колонок автора.
    charts: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ReplayedChart(BaseModel):
    spec: dict[str, Any]
    result_table: str | None = None


class ReplayOut(BaseModel):
    """Итог пересчёта; ``check`` без ``bound`` — таблицы автора у получателя нет."""

    check: TableCheck | None = None
    charts: dict[str, ReplayedChart] = Field(default_factory=dict)
    dashboards: list[dict[str, Any]] = Field(default_factory=list)
    # turn_id или id плитки -> почему не пересчитан.
    failed: dict[str, str] = Field(default_factory=dict)


class Replayer:
    """Пересчёт одной сессии; помнит выполненные шаги, чтобы не считать их дважды.

    ``checks`` — таблицы автора, найденные у получателя; без них ищутся заново.
    """

    def __init__(
        self, memory: SessionMemory, db: DuckDBManager, checks: dict[str, TableCheck] | None = None
    ) -> None:
        self.memory, self.db = memory, db
        self.executor = StepExecutor(db=db)
        self.turns = {turn["turn_id"]: turn for turn in memory.turns}
        self.author_table = (memory.source or {}).get("table")
        if checks is None:
            checks = check_tables(db, BundleSource.model_validate(memory.source or {}))
        self.checks = checks
        self.renames = {table: check.bound for table, check in checks.items() if check.bound}
        self.tables: dict[tuple[str, int], str] = {}
        self.errors: dict[tuple[str, int], str] = {}

    async def run(self, specs: dict[str, dict]) -> tuple[ReplayOut, list[Dashboard]]:
        """Пересчитать графики ходов и плитки дашбордов сессии."""
        out = ReplayOut()
        charts: dict[str, str] = {}
        for turn in self.memory.turns:
            if not turn.get("replay") or turn.get("status") != "ok":
                continue
            turn_id = turn["turn_id"]
            ref = Replay.model_validate(turn["replay"])
            try:
                if ref.overview:
                    out.charts[turn_id] = ReplayedChart(spec=self.overview(turn))
                    continue
                table = await self.table(ref)
                spec = specs.get(turn_id) or fallback_spec(turn)
                out.charts[turn_id] = ReplayedChart(
                    spec=self.rebuilt(spec, table), result_table=table
                )
                charts[turn_id] = table
            except Exception as exc:  # noqa: BLE001 — ход остаётся с причиной
                out.failed[turn_id] = reason(exc)
        dashboards = await self.dashboards(out.failed)
        results: dict[str, list[str]] = {}
        for (turn_id, _), table in self.tables.items():
            results.setdefault(turn_id, []).append(table)
        self.memory.record_replay(results, charts)
        return out, dashboards

    async def table(self, ref: Replay) -> str:
        """Таблица шага ``ref`` у получателя; ``ReplayError`` — шаг не выполнить."""
        turn = self.turns.get(ref.turn_id or "")
        steps = chain(turn.get("steps") or [], ref.step_id) if turn is not None else None
        if not steps:
            raise ReplayError("Не найден шаг, по которому построен график.")
        source = self.source(turn)
        current, done = source, {}
        for raw in steps:
            key = (turn["turn_id"], raw["step_id"])
            if key in self.errors:
                raise ReplayError(self.errors[key])
            try:
                step = self.step(raw)
                table = self.tables.get(key) or await self.executor.execute(
                    step, current, step_tables=done, source_table=source, history=self.memory
                )
            except Exception as exc:
                logger.exception("пересчёт: шаг %s хода %s не выполнен", *reversed(key))
                self.errors[key] = f"Шаг {raw['step_id']} ({raw.get('tool')}): {reason(exc)}"
                raise ReplayError(self.errors[key]) from exc
            self.tables[key] = done[step.step_id] = table
            if tool_kind(step) == "transform":
                current = table
        return done[ref.step_id]

    def step(self, raw: dict) -> ResearchStep:
        """Шаг автора с таблицей получателя вместо таблицы автора."""
        if raw.get("tool") in PYTHON_TOOLS:
            raise ReplayError("Шаг с кодом Python у вас не выполняется.")
        return ResearchStep.model_validate(
            {
                **raw,
                "arguments": self.rename(raw.get("arguments") or {}),
                "columns": self.rename(raw.get("columns") or []),
            }
        )

    def source(self, turn: dict) -> str:
        """Исходная таблица хода у получателя; ``ReplayError`` — какой-то таблицы хода у него нет."""
        tables = turn.get("tables") or [self.author_table]
        if tables[0] is None:
            raise ReplayError("Неизвестно, по какой таблице сделан ход.")
        for table in tables:
            if table not in self.renames:
                raise ReplayError(not_found(table, self.checks.get(table)))
        return self.renames[tables[0]]

    def rename(self, value: Any) -> Any:
        if isinstance(value, list):
            return [self.rename(item) for item in value]
        if isinstance(value, dict):
            return {key: self.rename(item) for key, item in value.items()}
        if isinstance(value, str) and value in self.renames:
            return self.renames[value]
        return value

    def rebuilt(self, spec: dict, table: str) -> dict:
        records = self.db.to_records(table, limit=SPEC_ROW_LIMIT)
        return rebuild_spec(spec, table, ordered(records, spec))

    def overview(self, turn: dict) -> dict:
        """Таблица колонок по паспорту таблицы получателя — без LLM."""
        table = self.source(turn)
        rows = overview.profile_rows(
            overview.passport(self.db, table), ProfileMemory(self.db).load_enrichment(table)
        )
        return build_vega_spec("table", data=rows, title=OVERVIEW_CAPTION)

    async def dashboards(self, failed: dict[str, str]) -> list[Dashboard]:
        """Пересчитать плитки дашбордов сессии по их шагам; вернуть дашборды."""
        store = DashboardStore()
        refs = self.memory.replay_tiles
        out = []
        for summary in store.list(self.memory.session_id):
            dashboard = store.get(summary.id)
            if dashboard is None:
                continue
            changed = False
            for tile in dashboard.tiles:
                ref = refs.get(tile.id)
                if ref is None:
                    continue
                try:
                    table = await self.table(Replay.model_validate(ref))
                    records = self.db.to_records(table, limit=SPEC_ROW_LIMIT)
                except Exception as exc:  # noqa: BLE001 — плитка остаётся с причиной
                    failed[tile.id] = reason(exc)
                    continue
                tile.spec = rebuild_spec(tile.spec, table, ordered(records, tile.spec))
                tile.result_table = table
                tile.snapshot_data = snapshot_records(records)
                tile.snapshot_at = time.time()
                changed = True
            if changed:
                dashboard = store.update(dashboard.id, tiles=dashboard.tiles) or dashboard
            out.append(dashboard)
        return out


def ordered(records: list[dict], spec: dict) -> list[dict]:
    """Колонки результата в порядке графика автора: от него зависят оси.

    Нет каких-то колонок автора — строки как есть.
    """
    usermeta = spec.get("usermeta") if isinstance(spec.get("usermeta"), dict) else {}
    columns = [
        column["field"]
        for column in usermeta.get("columns") or []
        if isinstance(column, dict) and isinstance(column.get("field"), str)
    ]
    if not columns or not records or not set(columns) <= set(records[0]):
        return records
    return [{column: row[column] for column in columns} for row in records]


def fallback_spec(turn: dict) -> dict:
    """Спека по типу графика хода, если фронт не прислал спеку автора."""
    chart = turn.get("chart") or {}
    return {"usermeta": {"chart_type": chart.get("type") or "bar"}}


def not_found(table: str, check: TableCheck | None) -> str:
    """Почему ход не пересчитан: его таблицы у получателя нет."""
    if check is not None and check.status == "not_connected":
        return f"Таблица «{table}» не нашлась: база {check.dsn} у вас не подключена."
    if check is not None and check.status == "different":
        return f"Таблица «{table}» не нашлась: у вас таблица с этим именем, но с другими данными."
    return f"Таблица «{table}» у вас не нашлась."


def reason(exc: Exception) -> str:
    return str(exc).strip() or type(exc).__name__
