"""Планировщик: вопрос пользователя -> ResearchPlan (список шагов).

Отдаёт LLM вопрос, топ-K релевантных колонок и список тулов.
Результат валидируется по ResearchPlan (см. models/research_step.py).
"""

import difflib
import json
import logging
import re
from collections.abc import Callable

from app.config import settings
from app.core.agent.quality_fixes import select_tools
from app.core.agent.turn_log import describe_plan
from app.core.cache.models import cache_key, schema_hash
from app.core.cache.plan_cache import PlanCache
from app.core.llm.client import LLMClient
from app.core.llm.profiles import ModelPool

logger = logging.getLogger(__name__)
from app.core.agent.step_executor import tool_kind
from app.core.dashboards import templates
from app.core.dashboards.templates import DashboardTemplate
from app.core.data.relations import describe_related
from app.core.data.semantic_select import (
    describe_columns,
    describe_other_columns,
    select_columns,
)
from app.core.llm.prompts.quality_rules import PLANNER_RULES
from app.core.llm.prompts.step_planner import (
    DASHBOARD_CUSTOM,
    DASHBOARD_PLAN,
    DASHBOARD_TEMPLATE,
    EMPTY_PLAN,
    IMPORTED_TURN,
    MEMORIES,
    NEW_TOOLS,
    NO_TEMPLATE_TILES,
    NO_TILES,
    OLDER_TURNS,
    PAST_TURNS,
    PLAN_ERRORS,
    REPLY_DRAFT,
    STEP_FIX,
    STEP_PLANNER,
)
from app.core.tools.registry import tool_registry
from app.models.research_step import (
    PREVIOUS,
    SOURCE,
    DashboardTilePlan,
    ResearchPlan,
    ResearchStep,
)

# Сколько раз переспросить LLM, если план не прошёл валидацию.
PLAN_FIX_ATTEMPTS = 1

# Колбэк реальных фаз планирования: semantic -> llm -> validation.
SubstageCallback = Callable[[str], None]

# Ответ хода без headline: ход упал или ждал уточнения.
NO_ANSWER = {"failed": "не получен (ход упал)", "clarification": "не получен (ход ждал уточнения)"}

DASHBOARD_WORDS = re.compile(r"дашборд|панел|сводк|dashboard", re.IGNORECASE)


class PlanInvalid(Exception):
    """План не прошёл валидацию и после всех переспросов; errors — ошибки валидатора."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


class StepPlanner:
    """Планировщик исследования на базе LLM."""

    def __init__(
        self,
        llm: LLMClient | None = None,
        pool: ModelPool | None = None,
        cache: PlanCache | None = None,
    ) -> None:
        self.llm = llm or (pool.get("planner") if pool is not None else LLMClient())
        self.cache = cache

    async def plan(
        self,
        question: str,
        context: dict,
        on_substage: SubstageCallback | None = None,
    ) -> ResearchPlan:
        """Сгенерировать план шагов под вопрос.

        ``on_substage`` получает реальные фазы: ``semantic`` (отбор колонок и
        инструментов), ``llm`` (вызов модели), ``validation`` (проверка плана).
        """

        def substage(name: str) -> None:
            if on_substage is not None:
                on_substage(name)

        substage("semantic")
        tools = tool_registry.describe()
        columns = all_columns = context.get("columns", [])
        profile = context.get("profile")
        if profile is not None:
            # Отбираем топ-5 релевантных колонок (семантический отбор, docs/architecture.md)
            columns = select_columns(
                question, columns, profile=profile, titles=context.get("titles")
            )
        start = int(context.get("next_step_id", 0))
        cache_key_obj = None
        if (
            self.cache is not None
            and start == 0
            and not context.get("executed")
            and not context.get("reply")
        ):
            cache_key_obj = cache_key(
                namespace="plan",
                question=question,
                table=str(context.get("datasource") or ""),
                schema=schema_hash(list(all_columns), context.get("descriptions")),
                model=str(getattr(self.llm, "model", "")),
                prompt_version="step_planner_v1",
            )
            cached = self.cache.get(cache_key_obj)
            if cached is not None:
                logger.info("план из кеша: %s", describe_plan(cached))
                return cached
        semantic_model = context.get("semantic_model")
        semantic_block = ""
        if settings.agent_semantic_layer and semantic_model is not None:
            from app.core.data.semantic_compiler import SemanticCompiler
            from app.core.data.semantic_planner import SemanticPlanner

            semantic_planner = SemanticPlanner()
            intent = semantic_planner.map(question, semantic_model)
            semantic_block = semantic_planner.to_prompt(semantic_model, intent)
            if intent.metrics and not is_dashboard_question(question):
                try:
                    semantic_plan = SemanticCompiler().compile(intent, semantic_model)
                except ValueError:
                    semantic_plan = None
                if semantic_plan is not None:
                    renumber_steps(semantic_plan, start)
                    errors = validate_plan(
                        semantic_plan,
                        known_steps=start,
                        columns=set(all_columns) if profile else None,
                        question=question,
                    )
                    if not errors:
                        logger.info("план (semantic layer): %s", describe_plan(semantic_plan))
                        return semantic_plan
                    logger.warning("semantic layer: план отклонён: %s", errors)
        if settings.agent_tool_topk:
            tools = select_tools(question)
        if not context.get("related") and not context.get("catalog"):
            # Связанных таблиц нет — join_related не нужен, промпт как без связей (#165).
            tools = "\n".join(
                line for line in tools.split("\n") if not line.startswith("- join_related ")
            )
        described = describe_columns(
            columns, profile, context.get("descriptions"), context.get("titles")
        )
        others = describe_other_columns(
            [name for name in all_columns if name not in columns],
            profile,
            context.get("titles"),
            question=question,
        )
        template = None
        # Плитка дашборда по кнопке (#224) — обычный вопрос, без шаблонов и плиток.
        if not context.get("tile") and is_dashboard_question(question):
            template = templates.match(all_columns, context.get("titles"))
            logger.info("    дашборд: шаблон=%s", template.id if template else None)

        described = f"{described}\n{others}" if others else described
        related = describe_related(context.get("datasource"), context.get("related", []))
        tables = [block for block in (related, context.get("catalog")) if block]

        def build_prompt(tools_text: str) -> str:
            text = STEP_PLANNER.format(
                tools=tools_text,
                columns="\n\n".join([described, *tables]),
                memories=describe_memories(context.get("memories", [])),
                history=describe_history(context),
                executed=context.get("executed", "нет"),
                next_step_id=start,
                question=question,
            )
            if settings.agent_quality_fixes:
                text += PLANNER_RULES
            created = context.get("created_tools") or []
            if created:
                described_tools = [
                    f"- {item.tool.describe()}" if getattr(item, "tool", None) else f"- {item}"
                    for item in created
                ]
                text += NEW_TOOLS.format(tools="\n".join(described_tools))
            if semantic_block:
                text += "\n\nСемантический слой:\n" + semantic_block
            if is_dashboard_question(question):
                text += dashboard_prompt(template, all_columns, context.get("titles"))
            return text

        prompt = build_prompt(tools)
        schema_override = None
        if settings.agent_typed_schema:
            from app.core.tools.schema import compact_planner_schema

            schema_override = compact_planner_schema(
                tool_registry.planner_tools(), settings.agent_max_steps
            )
        logger.debug(">>> StepPlanner.plan() вопрос=%r", question)
        logger.debug("    промпт=%r", prompt)
        substage("llm")
        plan = await self._ask(prompt, start, template, schema_override)
        if not plan.steps and start == 0 and plan.route == "data" and not plan.remember:
            # Без размышлений модель отдаёт пустой план и на вопросы о данных
            # («что с выручкой каждый июль?»); второй шанс — один вызов.
            # Вопрос о прошлом ответе (route=answer), обзор и «запомни» — не переспрашиваем.
            logger.warning("    план пустой, переспрос")
            substage("llm")
            plan = await self._ask(prompt + EMPTY_PLAN, start, template, schema_override)
        # Проверяем и ответ на переспрос: непроверенный план упал бы уже при
        # исполнении, потратив попытки fix_step и показав ошибку шага.
        # Колонки проверяем при наличии паспорта: без него список из каталога
        # может быть неполным, а точную ошибку всё равно поймает исполнитель.
        substage("validation")
        plan_columns = set(all_columns) if profile else None
        errors = validate_plan(
            plan,
            known_steps=start,
            columns=plan_columns,
            question=question,
        )
        for _ in range(PLAN_FIX_ATTEMPTS):
            if not errors:
                break
            logger.warning("    план не прошёл валидацию: %s", errors)
            fix = PLAN_ERRORS.format(plan=_plan_json(plan), errors="\n".join(errors))
            if settings.agent_tool_topk and tools != tool_registry.describe():
                # Retrieval мог не включить нужный тул — вторая попытка видит весь список.
                logger.info("    повтор с полным списком тулов")
                prompt = build_prompt(tool_registry.describe())
            substage("llm")
            plan = await self._ask(prompt + fix, start, template, schema_override)
            substage("validation")
            errors = validate_plan(
                plan,
                known_steps=start,
                columns=plan_columns,
                question=question,
            )
        if errors and errors == [template_gap(plan)]:
            logger.warning("<<< StepPlanner.plan() шаблон не собран, дашборд без шаблона")
            plan.template_id = None
            errors = []
        if errors:
            logger.warning("<<< StepPlanner.plan() план отклонён после переспроса: %s", errors)
            raise PlanInvalid(errors)
        if cache_key_obj is not None:
            self.cache.put(cache_key_obj, plan, ttl_s=settings.agent_cache_ttl_s)
        logger.debug("<<< StepPlanner.plan() шагов=%d", len(plan.steps))
        return plan

    async def fix_step(
        self, step: ResearchStep, error: str, columns: dict[str, str], seen: set[int]
    ) -> ResearchStep:
        """Исправить упавший шаг по тексту ошибки и колонкам его входной таблицы.

        step_id и purpose сохраняются, инструмент может смениться. Шаг,
        не прошедший валидацию, — ValueError с самим отклонённым шагом и
        ошибками валидации (их получит следующая попытка исправления).
        """
        from app.core.llm.prompts.system_analyst import SYSTEM_ANALYST

        prompt = STEP_FIX.format(
            tools=tool_registry.describe(),
            step=_step_json(step),
            error=error,
            columns="\n".join(f"{name}: {kind}" for name, kind in columns.items()) or "нет",
            executed=", ".join(f"$step_{ref}" for ref in sorted(seen)) or "нет",
        )
        logger.info(">>> StepPlanner.fix_step() шаг %d ошибка=%r", step.step_id, error)
        fixed = await self.llm.complete_structured(SYSTEM_ANALYST, prompt, ResearchStep)
        fixed = fixed.model_copy(
            update={"step_id": step.step_id, "purpose": fixed.purpose or step.purpose}
        )
        errors = _validate_step(fixed, seen)
        if errors:
            raise ValueError(f"Исправление {_step_json(fixed)} отклонено: {'; '.join(errors)}")
        logger.info("<<< StepPlanner.fix_step() tool=%s args=%s", fixed.tool, fixed.arguments)
        return fixed

    async def _ask(
        self,
        prompt: str,
        start: int,
        template: DashboardTemplate | None = None,
        schema_override: dict | None = None,
    ) -> ResearchPlan:
        from app.core.llm.prompts.system_analyst import SYSTEM_ANALYST

        kwargs = {"schema_override": schema_override} if schema_override else {}
        plan = await self.llm.complete_structured(SYSTEM_ANALYST, prompt, ResearchPlan, **kwargs)
        # LLM может не включать step_id в шаги — присваиваем последовательные ID.
        renumber_steps(plan, start)
        if template is not None:
            plan.dashboard_requested = True
            apply_template(plan, template)
        return plan


def describe_memories(memories: list[str]) -> str:
    """Блок воспоминаний промпта; без воспоминаний блока нет."""
    if not memories:
        return ""
    return MEMORIES.format(memories="\n".join(f"- {text}" for text in memories))


def describe_history(context: dict) -> str:
    """Блок истории промпта: черновик из цепочки ответа или прошлые ходы."""
    chain = context.get("reply") or []
    if not chain:
        turns = context.get("turns", [])
        first = turns[0].get("number", 1) if turns else 1
        older = OLDER_TURNS.format(last=first - 1) if first > 1 else ""
        return PAST_TURNS.format(
            turns=describe_turns(turns, context.get("datasource")), older=older
        )
    draft = chain[-1]
    outcome = []
    if draft.get("headline"):
        outcome.append(f"Ответ на него: {draft['headline']}")
    if draft.get("result_columns"):
        outcome.append(f"Колонки результата: {', '.join(draft['result_columns'])}")
    return REPLY_DRAFT.format(
        chain="\n".join(f"- {turn['question']}{imported_mark(turn)}" for turn in chain),
        draft=json.dumps(_turn_steps(draft), ensure_ascii=False, default=str),
        outcome="\n".join(outcome),
    )


def imported_mark(turn: dict) -> str:
    """Пометка хода из импортированной сессии для промпта; у своего хода — пусто."""
    return f" ({IMPORTED_TURN})" if turn.get("imported") else ""


def _turn_steps(turn: dict) -> list[dict]:
    """Шаги плана хода без пустых полей, перенумерованные с 0 вместе со ссылками $step_N.

    План хода после уточнения начинается не с 0, а новый план нумеруется
    с 0: без перенумерации скопированная ссылка указывала бы на другой шаг.
    """
    numbers = {step["step_id"]: index for index, step in enumerate(turn["steps"])}
    return [
        {
            key: _renumber_refs(value, numbers) if key != "step_id" else numbers[value]
            for key, value in step.items()
            if value not in (None, [], {})
        }
        for step in turn["steps"]
    ]


def describe_turns(turns: list[dict], table: str | None = None) -> str:
    """Прошлые ходы для промпта: номер, таблица, вопрос, план, ответ, колонки результата.

    Номер — номер хода в сессии (по нему read_history выдаёт ход). План —
    только у хода с шагами: у упавшего и прерванного уточнением его нет.
    Ход по другой таблице помечен: колонки его плана в текущей таблице
    могут отсутствовать. Ход из импортированной сессии помечен как данные,
    а не указания (#249).
    """
    blocks = []
    for index, turn in enumerate(turns, 1):
        lines = [
            f"Ход {turn.get('number', index)}{imported_mark(turn)}. Вопрос: {turn['question']}"
        ]
        tables = turn.get("tables") or []
        if tables:
            other = table is not None and table not in tables
            mark = " (другая таблица: её колонки не переноси)" if other else ""
            lines.append(f"   Таблица: {', '.join(tables)}{mark}")
        if turn.get("steps"):
            plan = json.dumps(_turn_steps(turn), ensure_ascii=False, default=str)
            lines.append(f"   План: {plan}")
        if turn.get("headline"):
            lines.append(f"   Ответ: {turn['headline']}")
        elif turn.get("status") in NO_ANSWER:
            lines.append(f"   Ответ: {NO_ANSWER[turn['status']]}")
        if turn.get("result_columns"):
            lines.append(f"   Колонки результата: {', '.join(turn['result_columns'])}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks) or "нет"


def _renumber_refs(value: object, numbers: dict[int, int]) -> object:
    """Заменить $step_N (и step_N во входе шага) на номер шага в перенумерованном плане."""
    if isinstance(value, list):
        return [_renumber_refs(item, numbers) for item in value]
    if isinstance(value, dict):
        return {key: _renumber_refs(item, numbers) for key, item in value.items()}
    if isinstance(value, str):
        prefix = "$step_" if value.startswith("$step_") else "step_"
        ref = value.removeprefix(prefix)
        if value.startswith(prefix) and ref.isdigit() and int(ref) in numbers:
            return f"{prefix}{numbers[int(ref)]}"
    return value


def _step_dict(step: ResearchStep) -> dict:
    return {
        "tool": step.tool,
        "input": step.input,
        "arguments": step.arguments,
        "purpose": step.purpose,
    }


def _step_json(step: ResearchStep) -> str:
    return json.dumps(_step_dict(step), ensure_ascii=False, default=str)


def _plan_json(plan: ResearchPlan) -> str:
    """План в том виде, в каком его просили у модели (для переспроса)."""
    steps = [_step_dict(s) for s in plan.steps]
    payload = {"steps": steps, "final_viz": plan.final_viz, "result_step": plan.result_step}
    if plan.remember:
        payload["remember"] = plan.remember
    if plan.dashboard_requested:
        payload["dashboard_requested"] = True
        payload["dashboard_title"] = plan.dashboard_title
        payload["dashboard_tiles"] = [tile.model_dump() for tile in plan.dashboard_tiles]
    return json.dumps(payload, ensure_ascii=False, default=str)


_PLACEHOLDER = "ref_table"


def _without_refs(value: object) -> object:
    """Заменить ссылки $step_N/$prev/$source на валидное имя (для проверки)."""
    if isinstance(value, list):
        return [_without_refs(item) for item in value]
    if isinstance(value, dict):
        return {key: _without_refs(item) for key, item in value.items()}
    if isinstance(value, str) and (value in (PREVIOUS, SOURCE) or value.startswith("$step_")):
        return _PLACEHOLDER
    return value


def validate_plan(
    plan: ResearchPlan,
    known_steps: int = 0,
    columns: set[str] | None = None,
    question: str = "",
) -> list[str]:
    """Проверить шаги до выполнения: лимит, инструмент, аргументы, ссылки, колонки.

    ``columns`` — колонки исходной таблицы: ошибка в имени колонки ловится
    до выполнения, а не после падения шага. ``question`` нужен для проверки
    «для динамики нет date_column»: такая ошибка дешевле переспроса.
    """
    errors: list[str] = []
    if len(plan.steps) > settings.agent_max_steps:
        errors.append(f"в плане {len(plan.steps)} шагов, максимум {settings.agent_max_steps}")
    seen = set(range(known_steps))
    for index, step in enumerate(plan.steps):
        errors.extend(_validate_step(step, seen))
        if columns:
            errors.extend(_validate_step_columns(step, columns, index=index, plan=plan, seen=seen))
        seen.add(step.step_id)
    if question:
        errors.extend(_validate_time_hint(plan, question))
    # План, который кончается разведкой (lookup), всё равно будет достроен —
    # result_step может указывать на будущие шаги, сбрасываем его без переспроса.
    ends_with_lookup = bool(plan.steps) and tool_kind(plan.steps[-1]) == "lookup"
    if ends_with_lookup and plan.result_step not in seen:
        plan.result_step = None
    if plan.result_step is not None and plan.result_step not in seen:
        errors.append(f"result_step={plan.result_step}: такого шага нет")
    if ends_with_lookup:
        plan.dashboard_tiles = [tile for tile in plan.dashboard_tiles if tile.step_id in seen]
    else:
        errors.extend(_validate_tiles(plan, seen))
    return errors


def _validate_step_columns(
    step: ResearchStep,
    columns: set[str],
    *,
    index: int,
    plan: ResearchPlan,
    seen: set[int],
) -> list[str]:
    """Колоночные аргументы шага над исходной таблицей должны быть в схеме."""
    if not step.tool:
        return []
    # Шаг читает не источник: предыдущий transform мог переименовать/создать колонки.
    if step.input.startswith("$step_") and step.input[6:].isdigit():
        return []
    # Известные шаги (recovery) могли переименовать колонки — не угадываем.
    if step.input == PREVIOUS and seen:
        return []
    if step.input == PREVIOUS and _has_previous_transform(plan, index):
        return []
    if step.input not in (PREVIOUS, SOURCE):
        return []
    try:
        tool = tool_registry.get(step.tool)
        args = tool.parse_args(_without_refs(step.columns), _without_refs(step.arguments))
    except (KeyError, ValueError):
        # Основные ошибки инструмента/аргументов уже собраны _validate_step.
        return []
    errors: list[str] = []
    for name in tool.column_fields():
        value = getattr(args, name, None)
        for item in value if isinstance(value, list) else [value]:
            if not isinstance(item, str) or item.startswith("$") or item in columns:
                continue
            hint = difflib.get_close_matches(item, sorted(columns), n=1)
            suffix = f"; возможно, имелось в виду {hint[0]!r}" if hint else ""
            errors.append(f"шаг {step.step_id}: колонки {item!r} нет в таблице{suffix}")
    return errors


def _has_previous_transform(plan: ResearchPlan, index: int) -> bool:
    """Был ли до шага transform (тогда $prev — уже не исходная таблица)."""
    return any(tool_kind(step) == "transform" for step in plan.steps[:index])


_TIME_HINT = re.compile(
    r"динамик|тренд|по\s+месяц|по\s+недел|по\s+квартал|по\s+год|во\s+времени|по\s+дням",
    re.IGNORECASE,
)


def _validate_time_hint(plan: ResearchPlan, question: str) -> list[str]:
    """Вопрос про динамику требует date_column или отдельного time-шага."""
    if not _TIME_HINT.search(question):
        return []
    has_time_step = any(
        step.tool in ("time_bucket", "time_series", "trend_analysis") for step in plan.steps
    )
    for step in plan.steps:
        if step.tool != "revenue_analysis":
            continue
        arguments = step.arguments or {}
        has_date = bool(arguments.get("date_column"))
        if not has_date and not has_time_step:
            return ["для динамики нужен date_column в revenue_analysis или time_bucket"]
    return []


def _validate_tiles(plan: ResearchPlan, seen: set[int]) -> list[str]:
    errors = [
        f"плитка «{tile.title}»: step_id={tile.step_id} — такого шага нет"
        for tile in plan.dashboard_tiles
        if tile.step_id not in seen
    ]
    gap = template_gap(plan)
    if gap is not None:
        errors.append(gap)
    elif plan.dashboard_requested and not plan.dashboard_tiles:
        errors.append(NO_TILES)
    return errors


def template_gap(plan: ResearchPlan) -> str | None:
    template = next((item for item in templates.TEMPLATES if item.id == plan.template_id), None)
    if template is None:
        return None
    planned = {tile.title for tile in plan.dashboard_tiles}
    missing = [tile.title for tile in template.tiles if tile.title not in planned]
    if not missing:
        return None
    return NO_TEMPLATE_TILES.format(missing=", ".join(f"«{title}»" for title in missing))


def _validate_step(step: ResearchStep, seen: set[int]) -> list[str]:
    prefix = f"шаг {step.step_id}"
    if not step.tool:
        return [f"{prefix}: не указан инструмент"]
    try:
        tool = tool_registry.get(step.tool)
    except KeyError:
        return [f"{prefix}: неизвестный инструмент {step.tool!r}"]
    errors = []
    step_input = f"${step.input}" if step.input.startswith("step_") else step.input
    for ref in _step_refs([step_input, step.arguments, step.columns]):
        if ref not in seen:
            errors.append(f"{prefix}: ссылка $step_{ref} на ещё не выполненный шаг")
    try:
        tool.parse_args(_without_refs(step.columns), _without_refs(step.arguments))
    except ValueError as exc:
        errors.append(f"{prefix}: {exc}")
    return errors


def _step_refs(value: object) -> list[int]:
    if isinstance(value, list):
        return [ref for item in value for ref in _step_refs(item)]
    if isinstance(value, dict):
        return [ref for item in value.values() for ref in _step_refs(item)]
    if isinstance(value, str) and value.startswith("$step_") and value[6:].isdigit():
        return [int(value[6:])]
    return []


def is_dashboard_question(question: str) -> bool:
    return DASHBOARD_WORDS.search(question) is not None


def dashboard_prompt(
    template: DashboardTemplate | None, columns: list[str], titles: dict[str, str] | None
) -> str:
    if template is None:
        return DASHBOARD_PLAN.format(tiles=DASHBOARD_CUSTOM)
    roles = templates.role_columns(template, columns, titles) or {}
    tiles = "\n".join(
        f"- «{tile.title}» (chart_type {tile.chart_type}"
        f"{', is_kpi true' if tile.is_kpi else ''}"
        f"{', insight_needed true' if tile.insight_needed else ''}): {tile.purpose}"
        for tile in template.tiles
    )
    described = DASHBOARD_TEMPLATE.format(
        name=template.name,
        roles=", ".join(f"{role} — {column}" for role, column in roles.items()),
        tiles=tiles,
    )
    return DASHBOARD_PLAN.format(tiles=described)


def renumber_steps(plan: ResearchPlan, start: int) -> None:
    numbers = {step.step_id: start + index for index, step in enumerate(plan.steps)}
    if len(numbers) == len(plan.steps):
        for tile in plan.dashboard_tiles:
            tile.step_id = numbers.get(tile.step_id, tile.step_id)
    for index, step in enumerate(plan.steps):
        step.step_id = start + index


def apply_template(plan: ResearchPlan, template: DashboardTemplate) -> None:
    planned = list(plan.dashboard_tiles)
    by_title = {templates.normalize(tile.title): tile for tile in planned}
    found = [by_title.get(templates.normalize(tile.title)) for tile in template.tiles]
    leftovers = [tile for tile in planned if all(tile is not match for match in found)]
    spare = iter(leftovers if len(planned) == len(template.tiles) else [])
    tiles = []
    for template_tile, match in zip(template.tiles, found, strict=True):
        source = match or next(spare, None)
        if source is None:
            logger.warning("    плитка шаблона %r без шага", template_tile.title)
            continue
        tiles.append(
            DashboardTilePlan(
                title=template_tile.title,
                step_id=source.step_id,
                chart_type=template_tile.chart_type,
                is_kpi=template_tile.is_kpi,
                insight_needed=template_tile.insight_needed,
            )
        )
    plan.dashboard_tiles = tiles
    plan.template_id = template.id
    plan.dashboard_title = plan.dashboard_title or template.name
