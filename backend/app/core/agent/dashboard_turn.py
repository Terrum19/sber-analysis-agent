"""Ход дашборда по кнопке (#224): план плиток -> шаги плиток -> выбор графика LLM.

Запрос пользователя (или выбранная идея) превращается в план из 4–5 плиток.
Каждая плитка — отдельный маленький вопрос: обычный планировщик и исполнитель
шагов, плитки идут по очереди (локальная модель держит один запрос за раз).
Упавшая плитка пропускается. Вид графика и колонки осей
выбирает LLM одним вызовом на все плитки, когда их результаты готовы;
выбор проверяется по данным (viz/chart_choice.py), неподошедший — один
повтор, дальше вид по intent.

Каждый этап шлёт событие: фронт показывает, что происходит, на каждом шаге.

После плана и каждой построенной плитки ход пишет чекпоинт: план и таблицы
готовых плиток. Остановленный или упавший ход продолжается с него
(``resume``): план не составляется заново, готовые плитки не пересчитываются,
строятся только остальные.

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
from app.core.agent.chart_pick import ask_chart
from app.core.agent.orchestrator import (
    DASHBOARD_CHECKPOINT,
    NO_TABLE_MESSAGE,
    AgentOrchestrator,
    ClarificationNeeded,
    Emit,
    Execution,
    _arrange,
    _done,
    _elapsed_ms,
    _plan_event,
    _progress_event,
    _require_steps,
    existing_results,
)
from app.core.dashboards.explain import explain_tile
from app.core.dashboards.generator import DashboardGenerator
from app.core.dashboards.ideas import describe_table, generate_ideas
from app.core.dashboards.models import DashboardTile
from app.core.llm.client import LLMClient
from app.core.llm.prompts.chart_selector import CHART_KINDS
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
from app.core.llm.usage import llm_scope
from app.core.memory.session_memory import SessionMemory
from app.core.viz.chart_choice import describe_columns, rejection, sample_rows, top_rows
from app.core.viz.chart_selector import fits, is_single_number
from app.core.viz.vega_spec_builder import (
    SPEC_ROW_LIMIT,
    build_vega_spec,
    column_aliases,
    infer_field_types,
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
from app.models.research_step import TILE_STEP_IDS, ResearchStep

logger = logging.getLogger(__name__)

# Какой вариант сборки плиток сравниваем (см. docstring модуля).
DASHBOARD_VARIANT = "plan_per_tile"

# Меньше плиток — это уже не дашборд.
MIN_BUILT_TILES = 2

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

    async def run(
        self, memory: SessionMemory, request: ChatRequest, resume: dict | None = None
    ) -> AsyncIterator[dict]:
        """События хода до done; сборка идёт задачей, её события отдаются сразу.

        resume — чекпоинт остановленного хода: сборка продолжается с него.
        """
        events: asyncio.Queue[dict | None] = asyncio.Queue()
        task = asyncio.create_task(self._build(memory, request, events.put_nowait, resume))
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

    async def _build(
        self, memory: SessionMemory, request: ChatRequest, emit: Emit, resume: dict | None = None
    ) -> str | None:
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
        logger.info("дашборд: вариант=%s", DASHBOARD_VARIANT)

        emit(_progress_event("outline", "started", turn_id=memory.turn_id))
        started_at = time.perf_counter()
        # Готовые плитки: таблица результата и выполненные шаги.
        done: dict[int, tuple[str, list[ResearchStep]]] = {}
        if resume is None:
            described = describe_table(self.db, table, request.question)
            with llm_scope(stage="outline"):
                outline = await self._outline_for(request, table, described, emit)
            tiles = outline.tiles[:MAX_TILES]
            title = outline.title.strip() or DASHBOARD_TITLE
        else:
            tiles = [OutlineTile.model_validate(tile) for tile in resume["tiles"]]
            title = resume["title"]
            saved = {int(key): tile for key, tile in resume["built"].items()}
            kept = existing_results(
                self.db, {key: tile["result_table"] for key, tile in saved.items()}
            )
            done = {
                key: (table_name, [ResearchStep.model_validate(s) for s in saved[key]["steps"]])
                for key, table_name in kept.items()
            }
            logger.info("дашборд продолжен: готовы плитки %s из %d", sorted(done), len(tiles))
        emit(
            _progress_event(
                "outline",
                "finished",
                total_steps=len(tiles),
                elapsed_ms=_elapsed_ms(started_at),
            )
        )
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

        def checkpoint() -> None:
            memory.save_checkpoint(
                {
                    "kind": DASHBOARD_CHECKPOINT,
                    "title": title,
                    "tiles": [tile.model_dump(mode="json") for tile in tiles],
                    "built": {
                        str(tile_id): {
                            "result_table": result,
                            "steps": [step.model_dump(mode="json") for step in steps],
                        }
                        for tile_id, (result, steps) in done.items()
                    },
                }
            )

        checkpoint()
        # По одной: локальная модель всё равно обрабатывает запросы последовательно
        for tile_id, tile in enumerate(tiles):
            if tile_id in done:
                emit(_progress_event("tile", "finished", tile_id=tile_id, purpose=tile.title))
                continue
            execution = await self._tile(memory, tile_id, tile, emit)
            if execution is not None:
                done[tile_id] = (execution.result_table, execution.steps)
                checkpoint()
        built = {tile_id: (tiles[tile_id], done[tile_id][0]) for tile_id in sorted(done)}
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
        with llm_scope(stage="chart"):
            choices = await self._choose_charts(built, records)
        emit(_progress_event("chart", "finished", elapsed_ms=_elapsed_ms(started_at)))

        emit(_progress_event("dashboard", "started"))
        dashboard_tiles = []
        for tile_id, (tile, result) in built.items():
            choice = choices[tile_id]
            data = records[tile_id]
            if (shown := top_rows(self.db, result, choice)) != result:
                result, data = shown, self.db.to_records(shown, limit=SPEC_ROW_LIMIT)
            spec = build_vega_spec(
                choice.chart_type,
                result,
                data=data,
                titles=titles,
                title=tile.title,
                x=choice.x,
                y=choice.y,
                color=choice.color,
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
            request.session_id, title, _arrange(dashboard_tiles), turn_id=memory.turn_id
        )
        emit(_progress_event("dashboard", "finished"))
        payload = DashboardPayload.from_dashboard(dashboard)
        emit({"type": "dashboard", "data": payload.model_dump(exclude_none=True)})

        failed = [tile.title for tile_id, tile in enumerate(tiles) if tile_id not in built]
        headline = f"Дашборд «{title}» готов: плиток {len(built)}. Что и как посчитано:"
        details = [
            f"{tile.title}: {explain_tile(done[tile_id][1], titles, records[tile_id])}."
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
            # Токены плитки: её план и её шаги (step_id внутри — свой счёт).
            with llm_scope(tile_id=tile_id):
                with llm_scope(stage="planning"):
                    plan = await self.agent._plan(tile.question, context)
                _require_steps(plan)
                tile_emit(_plan_event(plan.steps))
                execution = await self.agent._execute_plan(
                    memory, plan, memory.table, tile.question, emit=tile_emit, checkpoint=False
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
                kinds=CHART_KINDS,
                tiles="\n".join(
                    _describe_tile(tile_id, tile, records[tile_id])
                    for tile_id, (tile, _) in pending.items()
                ),
                retry=retry,
            )
            try:
                answer = await ask_chart(self.llm, prompt, ChartChoices)
            except TimeoutError:
                logger.warning("    выбор графика: модель не ответила и на повтор — вид по intent")
                break
            except Exception:
                logger.exception("    выбор графика: модель не ответила")
                break
            rejected = {tile_id: "нет выбора для плитки" for tile_id in pending}
            for choice in answer.charts:
                if choice.tile_id not in pending:
                    continue
                why = rejection(choice, records[choice.tile_id])
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
    return CHART_TILE.format(
        tile_id=tile_id,
        title=tile.title,
        intent=tile.intent,
        rows=len(records),
        columns=describe_columns(records),
        sample=sample_rows(records),
    )


def _by_intent(tile_id: int, tile: OutlineTile, records: list[dict]) -> ChartChoice:
    if is_single_number(records):
        return ChartChoice(tile_id=tile_id, chart_type="kpi", reason="одно число")
    chart_type = INTENT_CHARTS[tile.intent]
    types = infer_field_types(records)
    if records and fits(chart_type, records, types):
        return ChartChoice(tile_id=tile_id, chart_type=chart_type, reason="по intent")
    return ChartChoice(tile_id=tile_id, chart_type="table", reason="ничего не подошло")
