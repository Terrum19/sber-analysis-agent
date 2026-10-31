"""Цикл проверки ответа (A-29): критик -> исправление по вердикту -> повторный критик.

Черновой ответ (revision 0) уже у пользователя. Критик выносит вердикт:
ok / caveat — ответ остаётся (оговорки — в limits); fix_insight — вывод
переписывается по тем же данным; extend_plan — к плану дописываются шаги;
replan — план заново. Исправленный ответ (revision 1) проверяется ещё раз,
но второго исправления нет: что осталось — оговорки.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from app.core.agent.assurance.critic import Critic, describe_issues, review_payload
from app.core.agent.assurance.evidence import build_evidence
from app.core.llm.prompts.review import EXTEND_PLAN, INSIGHT_REVIEW, REPLAN
from app.core.llm.usage import llm_scope
from app.models.insight import Insight
from app.models.research_step import ResearchPlan
from app.models.review import REPAIRS, Review

logger = logging.getLogger(__name__)

# Исправление не удалось: оговорка вместо исправленного ответа.
REPAIR_FAILED = "Проверка нашла: {problems}; исправить не удалось"
REVISION = 1


@dataclass
class Answer:
    """Ответ хода, который проверяется и может быть исправлен на месте."""

    execution: object
    insight: Insight
    spec: dict | None
    chart_table: str
    result_table: str


def _revised(event: dict) -> dict:
    """Этапы исправления — с revision: фронт показывает их отдельно от первого прогона."""
    if event["type"] == "progress":
        event["data"]["revision"] = REVISION
    return event


class Assurance:
    """Проверка ответа ходом оркестратора: его планировщик, исполнитель и инсайт."""

    def __init__(self, agent) -> None:
        self.agent = agent
        llm = getattr(agent.planner, "llm", None) or getattr(agent.insight_gen, "llm", None)
        self.critic = Critic(llm)

    async def run(self, memory, question: str, answer: Answer, titles: dict) -> AsyncIterator[dict]:
        """События проверки; answer меняется на месте, если ответ исправлен."""
        from app.core.agent.orchestrator import _progress_event

        draft = {"headline": answer.insight.headline, "details": list(answer.insight.details)}
        yield _progress_event("review", "started", round=1)
        try:
            with llm_scope(phase="review"):
                review = await self.critic.review(self._evidence(memory, question, answer, titles))
        except Exception as exc:
            # Проверка — дополнение: без неё остаётся обычный ответ.
            logger.exception("проверка: критик не ответил")
            yield _progress_event("review", "failed", round=1, error=str(exc))
            return
        yield _progress_event("review", "finished", round=1)
        action = review.verdict
        if action not in REPAIRS:
            self._caveats(answer, review)
            first = review_payload(review, 1, action)
            memory.record_review(first)
            yield {"type": "review", "data": first}
            return
        yield {"type": "review", "data": review_payload(review, 1, action)}
        reason = review.issues[0].message if review.issues else ""
        yield _progress_event("repair", "started", action=action, reason=reason)
        try:
            # Токены исправления — в repair/…: план, шаги, вывод и график заново.
            with llm_scope(phase="repair"):
                async for event in self._repair(memory, question, answer, review, titles):
                    yield event
        except Exception as exc:
            logger.exception("проверка: исправление %s не удалось", action)
            # Сбой шага исправления оставил ожидание уточнения: ход его не ждёт.
            memory.clear_pending_recovery()
            yield _progress_event("repair", "failed", action=action, error=str(exc))
            problems = "; ".join(issue.message for issue in review.issues)
            failed = review.model_copy(
                update={
                    "verdict": "caveat",
                    "caveats": [*review.caveats, REPAIR_FAILED.format(problems=problems)],
                }
            )
            self._caveats(answer, failed)
            payload = review_payload(failed, 1, "caveat")
            memory.record_review(payload)
            yield {"type": "review", "data": payload}
            return
        yield _progress_event("repair", "finished", action=action)

        yield _progress_event("review", "started", round=2)
        try:
            with llm_scope(phase="review"):
                final = await self.critic.review(
                    self._evidence(memory, question, answer, titles), round=2, previous=review
                )
        except Exception as exc:
            logger.exception("проверка: повторный критик не ответил")
            yield _progress_event("review", "failed", round=2, error=str(exc))
            memory.record_review(review_payload(review, 1, action), draft=draft)
            return
        yield _progress_event("review", "finished", round=2)
        self._caveats(answer, final)
        payload = review_payload(final, 2, final.verdict)
        memory.record_review(payload, draft=draft, first=review_payload(review, 1, action))
        yield {"type": "review", "data": payload}

    async def _repair(
        self, memory, question: str, answer: Answer, review: Review, titles: dict
    ) -> AsyncIterator[dict]:
        """Исправить ответ по вердикту: события исправления, answer — новый ответ."""
        from app.core.agent.orchestrator import _chart_event, _progress_event

        agent = self.agent
        execution = answer.execution
        if review.verdict in ("extend_plan", "replan"):
            build = self._extend if review.verdict == "extend_plan" else self._replan
            holder: list = []
            async for event in _streamed(
                lambda emit: build(memory, question, answer, review, emit), holder
            ):
                yield event
            execution = holder[0]
        picking = (
            agent._start_chart_pick(question, execution)
            if review.verdict != "fix_insight"
            else None
        )
        try:
            yield _revised(_progress_event("insight", "started"))
            insight = await agent.insight_gen.generate(
                question,
                execution.result_table,
                supporting=execution.supporting,
                steps=execution.steps,
                memories=memory.context_memories(),
                clarifications=memory.clarifications,
                result_tool=execution.result_tool,
                source=memory.table,
                inputs=execution.inputs,
                review=INSIGHT_REVIEW.format(
                    draft=_describe_insight(answer.insight), issues=describe_issues(review)
                ),
            )
            yield {
                "type": "text",
                "data": {
                    "headline": insight.headline,
                    "details": insight.details,
                    "revision": REVISION,
                },
            }
            answer.insight = insight
            answer.execution = execution
            if review.verdict == "fix_insight":
                # Данные те же: график прежний.
                return
            spec, chart_table, result_table = await agent._build_chart(
                question, execution, titles, picking
            )
            answer.result_table = result_table
            if spec is None:
                return
            yield _revised(_progress_event("chart", "started"))
            memory.record_chart(spec["usermeta"]["chart_type"], chart_table, insight.headline)
            answer.spec, answer.chart_table = spec, chart_table
            yield _chart_event(spec, insight.headline, chart_table, revision=REVISION)
        finally:
            if picking is not None:
                picking.cancel()

    async def _extend(self, memory, question: str, answer: Answer, review: Review, emit):
        """Дописать к плану шаги, которые закрывают проблемы, и выполнить их."""
        from app.core.agent.orchestrator import (
            _describe_executed,
            _next_step_id,
            _plan_event,
            _progress_event,
            _renumber,
        )

        agent, execution = self.agent, answer.execution
        context = memory.build_context(question)
        context["related"] = agent._related(memory.table, question)
        context["executed"] = _describe_executed(execution.steps, execution.tables)
        context["next_step_id"] = start = _next_step_id(execution.tables)
        context["review"] = EXTEND_PLAN.format(issues=describe_issues(review))
        emit(_progress_event("planning", "started"))
        added = _renumber(await agent._plan(question, context), start)
        if not added.steps:
            raise RuntimeError("планировщик не дополнил план")
        steps = [*execution.steps, *added.steps]
        emit(_plan_event(steps))
        plan = ResearchPlan(
            steps=steps,
            final_viz=execution.final_viz,
            dashboard_requested=execution.dashboard_requested,
            result_step=_result_step(execution, added),
        )
        return await agent._execute_plan(
            memory,
            plan,
            memory.table,
            question,
            step_tables=dict(execution.tables),
            emit=emit,
            checkpoint=False,
            verify=True,
        )

    async def _replan(self, memory, question: str, answer: Answer, review: Review, emit):
        """Новый план с замечаниями к прошлому и его выполнение."""
        from app.core.agent.orchestrator import (
            _describe_executed,
            _plan_event,
            _progress_event,
            _require_steps,
        )

        agent, execution = self.agent, answer.execution
        context = memory.build_context(question)
        context["related"] = agent._related(memory.table, question)
        context["review"] = REPLAN.format(
            plan=_describe_executed(execution.steps, execution.tables),
            issues=describe_issues(review),
        )
        emit(_progress_event("planning", "started"))
        plan = await agent._plan(question, context)
        _require_steps(plan)
        emit(_plan_event(plan.steps))
        return await agent._execute_plan(
            memory, plan, memory.table, question, emit=emit, checkpoint=False, verify=True
        )

    def _evidence(self, memory, question: str, answer: Answer, titles: dict) -> str:
        execution = answer.execution
        return build_evidence(
            self.agent.db,
            question=question,
            steps=execution.steps,
            tables=execution.tables,
            inputs=execution.inputs,
            result_table=execution.result_table,
            supporting=execution.supporting,
            findings=execution.findings,
            insight=answer.insight,
            source=memory.table,
            titles=titles,
            memories=memory.context_memories(),
        )

    @staticmethod
    def _caveats(answer: Answer, review: Review) -> None:
        """Оговорки проверки — в limits вывода (их видит экспорт и журнал хода)."""
        if not review.caveats:
            return
        limits = [answer.insight.limits] if answer.insight.limits else []
        answer.insight.limits = "; ".join([*limits, *review.caveats])


async def _streamed(
    build: Callable[[Callable[[dict], None]], Awaitable[object]], holder: list
) -> AsyncIterator[dict]:
    """События задачи build(emit) по мере появления; её результат — в holder."""
    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    task = asyncio.create_task(build(lambda event: queue.put_nowait(_revised(event))))
    task.add_done_callback(lambda _: queue.put_nowait(None))
    try:
        while (event := await queue.get()) is not None:
            yield event
        holder.append(await task)
    finally:
        task.cancel()


def _result_step(execution, added: ResearchPlan) -> int | None:
    """Шаг ответа дополненного плана: новый, если он продолжает конвейер, иначе прежний."""
    from app.core.agent.step_executor import tool_kind

    if added.result_step is not None:
        return added.result_step
    if tool_kind(added.steps[-1]) == "transform":
        return added.steps[-1].step_id
    return next(
        (step_id for step_id, table in execution.tables.items() if table == execution.result_table),
        None,
    )


def _describe_insight(insight: Insight) -> str:
    lines = [f"Заголовок: {insight.headline}", *(f"- {line}" for line in insight.details)]
    return "\n".join(lines)
