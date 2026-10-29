"""Ход дашборда по кнопке (#224): план плиток -> шаги плиток -> выбор графика LLM.

Запрос пользователя (или выбранная идея) превращается в план из 4–5 плиток.
Каждая плитка — отдельный маленький вопрос: обычный планировщик и исполнитель
шагов, плитки идут по очереди (локальная модель держит один запрос за раз).
Упавшая плитка пропускается. Вид графика
выбирает LLM одним вызовом на все плитки, когда их результаты готовы;
выбор проверяется fits, неподошедший — один повтор, дальше вид по intent.

Каждый этап шлёт событие: фронт показывает, что происходит, на каждом шаге.

Сейчас сравниваем два варианта сборки плиток с одним контрактом событий:
- plan_per_tile (эта ветка, feature/B-07-dashboard-buttons) — на каждую
  плитку свой вызов планировщика;
- quick_tiles (feature/B-07-dashboard-quick-tiles) — план дашборда сразу
  отдаёт поля простых плиток (показатель, агрегат, разрез, период), шаги
  собирает код, планировщик — только для сложных плиток.
Вариант пишется в лог хода (DASHBOARD_VARIANT), чтобы сравнивать прогоны.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator

from app.api.schemas.chat import ChatRequest, DashboardPayload
from app.core.agent.orchestrator import (
    NO_TABLE_MESSAGE,
    AgentOrchestrator,
    ClarificationNeeded,
    Emit,
    Execution,
    _arrange,
    _done,
    _elapsed_ms,
    _progress_event,
    _require_steps,
)
from app.core.dashboards.explain import explain_tile
from app.core.dashboards.generator import DashboardGenerator
from app.core.dashboards.ideas import describe_table, generate_ideas
from app.core.dashboards.models import DashboardTile
from app.core.llm.client import LLMClient
from app.core.llm.prompts.dashboard_builder import (
    CHART_CHOICE,
    CHART_RETRY,
    CHART_TILE,
    DASHBOARD_OUTLINE,
    OUTLINE_FORCED,
    OUTLINE_NO_TILES,
    OUTLINE_NOT_ENOUGH,
    OUTLINE_RETRY,
)
from app.core.llm.prompts.system_analyst import SYSTEM_ANALYST
from app.core.memory.session_memory import SessionMemory
from app.core.viz.chart_selector import fits, is_single_number
from app.core.viz.vega_spec_builder import (
    SPEC_ROW_LIMIT,
    build_vega_spec,
    column_aliases,
    infer_field_types,
    to_json_value,
)
from app.models.dashboard_plan import (
    MAX_TILES,
    MIN_TILES,
    ChartChoice,
    ChartChoices,
    DashboardIdea,
    DashboardOutline,
    OutlineTile,
)

logger = logging.getLogger(__name__)

# Какой вариант сборки плиток сравниваем (см. docstring модуля).
DASHBOARD_VARIANT = "plan_per_tile"

# Шаги плитки N нумеруются с (N + 1) * TILE_STEP_IDS: номера плиток не пересекаются.
TILE_STEP_IDS = 100
# Меньше плиток — это уже не дашборд.
MIN_BUILT_TILES = 2
# Строк результата в промпте выбора графика.
CHART_SAMPLE_ROWS = 10

# Выбор графика LLM не удался или не подошёл — вид по тому, что плитка показывает.
INTENT_CHARTS = {
    "kpi": "kpi",
    "compare": "bar",
    "trend": "line",
    "distribution": "histogram",
    "relation": "scatter",
    "share": "bar",
}

# Сколько символов сырого ответа модели писать в лог при плане без плиток.
RAW_LOG_CHARS = 6000

DASHBOARD_TITLE = "Дашборд"
FALLBACK_MESSAGE = (
    "Запрос общий — собрал дашборд по варианту «{title}». Можно выбрать другой вариант."
)
TOO_FEW_TILES = "Не удалось собрать дашборд: построено плиток {built} из {total}."
DASHBOARD_FAILED = "Не удалось собрать дашборд, попробуйте ещё раз"


class DashboardTurn:
    """Один ход в режиме дашборда; агент даёт планировщик, исполнитель и DuckDB."""

    def __init__(self, agent: AgentOrchestrator) -> None:
        self.agent = agent
        self.db = agent.db
        self.llm = getattr(agent.planner, "llm", None) or LLMClient()

    async def run(self, memory: SessionMemory, request: ChatRequest) -> AsyncIterator[dict]:
        """События хода до done; сборка идёт задачей, её события отдаются сразу."""
        events: asyncio.Queue[dict | None] = asyncio.Queue()
        task = asyncio.create_task(self._build(memory, request, events.put_nowait))
        task.add_done_callback(lambda _: events.put_nowait(None))
        try:
            while (event := await events.get()) is not None:
                yield event
            headline = await task
        except Exception as exc:
            logger.exception("<<< дашборд: ход упал")
            memory.finish_turn("failed")
            yield _progress_event("dashboard", "failed", error=str(exc))
            yield {"type": "error", "data": {"message": DASHBOARD_FAILED}}
            yield _done(memory.turn_id)
            return
        finally:
            if not task.done():
                task.cancel()
        if headline is None:
            memory.finish_turn("failed")
        else:
            memory.finish_turn("ok", headline=headline)
        yield _done(memory.turn_id)

    async def _build(self, memory: SessionMemory, request: ChatRequest, emit: Emit) -> str | None:
        """Собрать дашборд; None — ошибка уже отправлена событием error."""
        table = memory.table
        if table is None:
            emit({"type": "error", "data": {"message": NO_TABLE_MESSAGE}})
            return None
        titles = column_aliases(
            {
                "glossary": self.agent.profiles.load_glossary(table),
                "titles": {
                    row.column: row.title for row in self.agent.profiles.load_enrichment(table)
                },
            }
        )
        described = describe_table(self.db, table, request.question)
        logger.info("дашборд: вариант=%s", DASHBOARD_VARIANT)

        emit(_progress_event("outline", "started"))
        started_at = time.perf_counter()
        outline = await self._outline_for(request, table, described, emit)
        tiles = outline.tiles[:MAX_TILES]
        emit(
            _progress_event(
                "outline",
                "finished",
                total_steps=len(tiles),
                elapsed_ms=_elapsed_ms(started_at),
            )
        )
        title = outline.title.strip() or DASHBOARD_TITLE
        emit(
            {
                "type": "dashboard_outline",
                "data": {
                    "title": title,
                    "tiles": [
                        {"tile_id": tile_id, "title": tile.title, "intent": tile.intent}
                        for tile_id, tile in enumerate(tiles)
                    ],
                },
            }
        )

        # По одной: локальная модель всё равно обрабатывает запросы последовательно
        executions = [
            await self._tile(memory, tile_id, tile, emit) for tile_id, tile in enumerate(tiles)
        ]
        built = {
            tile_id: (tiles[tile_id], execution.result_table)
            for tile_id, execution in enumerate(executions)
            if execution is not None
        }
        if len(built) < MIN_BUILT_TILES:
            message = TOO_FEW_TILES.format(built=len(built), total=len(tiles))
            emit(_progress_event("dashboard", "failed", error=message))
            emit({"type": "error", "data": {"message": message}})
            return None

        emit(_progress_event("chart", "started"))
        started_at = time.perf_counter()
        records = {
            tile_id: self.db.to_records(result, limit=SPEC_ROW_LIMIT)
            for tile_id, (_, result) in built.items()
        }
        choices = await self._choose_charts(built, records)
        emit(_progress_event("chart", "finished", elapsed_ms=_elapsed_ms(started_at)))

        emit(_progress_event("dashboard", "started"))
        dashboard_tiles = []
        for tile_id, (tile, result) in built.items():
            choice = choices[tile_id]
            data = _focus(records[tile_id], choice)
            spec = build_vega_spec(
                choice.chart_type, result, data=data, titles=titles, title=tile.title
            )
            dashboard_tiles.append(
                DashboardTile(
                    title=tile.title,
                    spec=spec,
                    result_table=result,
                    is_kpi=spec["usermeta"]["chart_type"] == "kpi",
                )
            )
        dashboard = await DashboardGenerator(db=self.db).generate(
            request.session_id, title, _arrange(dashboard_tiles)
        )
        emit(_progress_event("dashboard", "finished"))
        payload = DashboardPayload.from_dashboard(dashboard)
        emit({"type": "dashboard", "data": payload.model_dump(exclude_none=True)})

        failed = [tile.title for tile_id, tile in enumerate(tiles) if tile_id not in built]
        headline = f"Дашборд «{title}» готов: плиток {len(built)}. Что и как посчитано:"
        details = [
            f"{tile.title}: {explain_tile(executions[tile_id].steps, titles, records[tile_id])}."
            for tile_id, (tile, _) in built.items()
        ]
        if failed:
            details.append("Не удалось построить: " + ", ".join(failed) + ".")
        emit({"type": "text", "data": {"headline": headline, "details": details}})
        logger.info("<<< дашборд %s собран: %s", dashboard.id, details)
        return headline

    async def _outline_for(
        self, request: ChatRequest, table: str, described: str, emit: Emit
    ) -> DashboardOutline:
        """План по идее или по запросу; запроса не хватило — по первой идее."""
        if request.idea is not None:
            return await self._outline(_idea_request(request.idea), described, forced=True)
        outline = await self._outline(request.question, described, forced=False)
        if outline.enough_info and len(outline.tiles) >= MIN_BUILT_TILES:
            return outline
        logger.info("дашборд: запроса не хватило (%r), берём первую идею", request.question)
        ideas = await generate_ideas(self.db, table, self.llm)
        idea = ideas[0]
        emit(
            {
                "type": "dashboard_fallback",
                "data": {
                    "message": FALLBACK_MESSAGE.format(title=idea.title),
                    "idea": idea.model_dump(),
                    "alternatives": [other.model_dump() for other in ideas[1:]],
                },
            }
        )
        return await self._outline(_idea_request(idea), described, forced=True)

    async def _outline(self, text: str, described: str, forced: bool) -> DashboardOutline:
        """План плиток; по идее без плиток — один переспрос."""
        prompt = DASHBOARD_OUTLINE.format(
            min_tiles=MIN_TILES,
            max_tiles=MAX_TILES,
            request=text,
            table=described,
            forced=OUTLINE_FORCED if forced else "",
        )
        outline = await self._ask_outline(prompt)
        if forced and len(outline.tiles) < MIN_BUILT_TILES:
            logger.warning("дашборд: план по идее без плиток, переспрос с ошибкой")
            problem = OUTLINE_NO_TILES if outline.enough_info else OUTLINE_NOT_ENOUGH
            retry = OUTLINE_RETRY.format(problem=problem, min_tiles=MIN_TILES, max_tiles=MAX_TILES)
            outline = await self._ask_outline(prompt + retry)
            if len(outline.tiles) < MIN_BUILT_TILES:
                raise ValueError("Модель не составила план дашборда")
        logger.info(
            "дашборд: план %r enough_info=%s плитки=%s",
            outline.title,
            outline.enough_info,
            [(tile.title, tile.intent) for tile in outline.tiles],
        )
        return outline

    async def _ask_outline(self, prompt: str) -> DashboardOutline:
        """Вызов плана; без плиток — сырой ответ модели в лог, чтобы видеть причину."""
        outline = await self.llm.complete_structured(SYSTEM_ANALYST, prompt, DashboardOutline)
        if len(outline.tiles) < MIN_BUILT_TILES:
            raw = getattr(self.llm, "last_raw", None)
            logger.warning(
                "дашборд: в плане плиток %d, enough_info=%s, сырой ответ модели (%s симв.): %s",
                len(outline.tiles),
                outline.enough_info,
                len(raw) if raw is not None else "?",
                raw[:RAW_LOG_CHARS] if raw is not None else "недоступен",
            )
        return outline

    async def _tile(
        self,
        memory: SessionMemory,
        tile_id: int,
        tile: OutlineTile,
        emit: Emit,
    ) -> Execution | None:
        """Шаги одной плитки; вернуть итог выполнения или None, если плитка упала."""

        def tile_emit(event: dict) -> None:
            if event["type"] == "progress":
                event["data"]["tile_id"] = tile_id
            emit(event)

        emit(_progress_event("tile", "started", tile_id=tile_id, purpose=tile.title))
        started_at = time.perf_counter()
        try:
            context = memory.build_context(tile.question)
            context["next_step_id"] = (tile_id + 1) * TILE_STEP_IDS
            context["tile"] = True
            # Плитка «по регионам» на таблице заказов — через join_related (#165).
            context["related"] = self.agent._related(memory.table, tile.question)
            plan = await self.agent._plan(tile.question, context)
            _require_steps(plan)
            execution = await self.agent._execute_plan(
                memory, plan, memory.table, tile.question, emit=tile_emit
            )
        except Exception as exc:
            if isinstance(exc, ClarificationNeeded):
                # Уточнять у пользователя нечего: плитка просто не строится.
                memory.clear_pending_recovery()
                error = exc.error
            else:
                logger.exception("    плитка %d %r упала", tile_id, tile.title)
                error = str(exc)
            emit(
                _progress_event(
                    "tile",
                    "failed",
                    tile_id=tile_id,
                    purpose=tile.title,
                    elapsed_ms=_elapsed_ms(started_at),
                    error=error,
                )
            )
            return None
        emit(
            _progress_event(
                "tile",
                "finished",
                tile_id=tile_id,
                purpose=tile.title,
                elapsed_ms=_elapsed_ms(started_at),
            )
        )
        return execution

    async def _choose_charts(
        self, built: dict[int, tuple[OutlineTile, str]], records: dict[int, list[dict]]
    ) -> dict[int, ChartChoice]:
        """Вид графика каждой плитки: LLM по результатам, проверка fits, один повтор."""
        chosen: dict[int, ChartChoice] = {}
        pending = dict(built)
        rejected: dict[int, str] = {}
        for attempt in range(2):
            if not pending:
                break
            retry = (
                CHART_RETRY.format(
                    rejected="\n".join(f"- плитка {key}: {why}" for key, why in rejected.items())
                )
                if attempt
                else ""
            )
            prompt = CHART_CHOICE.format(
                tiles="\n".join(
                    _describe_tile(tile_id, tile, records[tile_id])
                    for tile_id, (tile, _) in pending.items()
                ),
                retry=retry,
            )
            try:
                answer = await self.llm.complete_structured(SYSTEM_ANALYST, prompt, ChartChoices)
            except Exception:
                logger.exception("    выбор графика: модель не ответила")
                break
            rejected = {tile_id: "нет выбора для плитки" for tile_id in pending}
            for choice in answer.charts:
                if choice.tile_id not in pending:
                    continue
                why = _rejection(choice, records[choice.tile_id])
                if why is None:
                    chosen[choice.tile_id] = choice
                    rejected.pop(choice.tile_id, None)
                    logger.info(
                        "    плитка %d: %s (%s)", choice.tile_id, choice.chart_type, choice.reason
                    )
                else:
                    rejected[choice.tile_id] = why
            pending = {tile_id: built[tile_id] for tile_id in rejected}
            if rejected:
                logger.warning("    выбор графика отклонён: %s", rejected)
        for tile_id, (tile, _) in pending.items():
            chosen[tile_id] = _by_intent(tile_id, tile, records[tile_id])
        return chosen


def _idea_request(idea: DashboardIdea) -> str:
    return f"{idea.title}. {idea.description}".strip()


def _describe_tile(tile_id: int, tile: OutlineTile, records: list[dict]) -> str:
    types = infer_field_types(records)
    columns = ", ".join(
        f"{column} ({kind}, уникальных {len({repr(row.get(column)) for row in records})})"
        for column, kind in types.items()
    )
    sample = [
        {key: to_json_value(value) for key, value in row.items()}
        for row in records[:CHART_SAMPLE_ROWS]
    ]
    return CHART_TILE.format(
        tile_id=tile_id,
        title=tile.title,
        intent=tile.intent,
        rows=len(records),
        columns=columns,
        sample=sample,
    )


def _rejection(choice: ChartChoice, records: list[dict]) -> str | None:
    """Почему выбор не подходит к строкам результата; None — подходит."""
    if not records:
        return "результат пуст"
    known = set(records[0])
    for name in (choice.x, choice.y, choice.color):
        if name is not None and name not in known:
            return f"колонки {name!r} нет в результате"
    if choice.chart_type == "kpi":
        return None if is_single_number(records) else "kpi — только когда результат одно число"
    data = _focus(records, choice)
    types = infer_field_types(data)
    column = (choice.y or choice.x) if choice.chart_type == "histogram" else None
    if column is not None and types.get(column) != "quantitative":
        column = None
    if fits(choice.chart_type, data, types, column):
        return None
    return f"{choice.chart_type} не подходит к {len(records)} строкам с колонками {list(types)}"


def _by_intent(tile_id: int, tile: OutlineTile, records: list[dict]) -> ChartChoice:
    if is_single_number(records):
        return ChartChoice(tile_id=tile_id, chart_type="kpi", reason="одно число")
    chart_type = INTENT_CHARTS[tile.intent]
    types = infer_field_types(records)
    if records and fits(chart_type, records, types):
        return ChartChoice(tile_id=tile_id, chart_type=chart_type, reason="по intent")
    return ChartChoice(tile_id=tile_id, chart_type="table", reason="ничего не подошло")


def _focus(records: list[dict], choice: ChartChoice) -> list[dict]:
    """Колонки x, y, color — первыми: построитель спеки берёт первые категорию и число."""
    if not records:
        return records
    first = [
        name
        for name in dict.fromkeys((choice.x, choice.y, choice.color))
        if name is not None and name in records[0]
    ]
    order = [*first, *(column for column in records[0] if column not in first)]
    return [{column: row.get(column) for column in order} for row in records]
