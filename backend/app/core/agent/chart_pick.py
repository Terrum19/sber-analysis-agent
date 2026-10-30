"""График ответа в чате: агент выбирает таблицу шага, вид и колонки осей.

Когда шаги хода выполнены, их результаты известны: колонки, типы, строки.
Один вызов LLM по описанию таблиц всех шагов выбирает, какую показать и
как (AnswerChart): «шаг 3, bar, x=store_id, y=sum_revenue, color=category».
Выбор проверяется по данным (viz/chart_choice.py), неподошедший — один
повтор с причиной. Нет выбора — None, и оркестратор строит график прежней
эвристикой (select_chart).

Таблица стат-теста в конце плана больше не отнимает график: модель видит
и таблицу, по которой тест считался.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import TypeVar

from app.config import settings
from app.core.agent.step_executor import tool_kind
from app.core.llm.prompts.chart_selector import (
    ANSWER_CHART,
    ANSWER_HINT,
    ANSWER_RETRY,
    CHART_KINDS,
    CHART_STEP,
)
from app.core.llm.prompts.system_analyst import SYSTEM_ANALYST
from app.core.tools.base import identifier
from app.core.viz.chart_choice import describe_columns, rejection, sample_rows, top_rows
from app.core.viz.vega_spec_builder import SPEC_ROW_LIMIT
from app.models.chart_choice import AnswerChart
from app.models.research_step import ResearchStep

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Повтор с причиной отказа — один: дальше дешевле эвристика, чем ещё вызов.
PICK_ATTEMPTS = 2
# Вызовов модели, если она не уложилась в chart_choice_timeout_s: первый и повтор.
CHOICE_CALLS = 2
# Виды планировщика, которые не подсказка к виду графика.
NO_HINT = ("auto", "none")


async def ask_chart(llm, prompt: str, schema: type[T]) -> T:
    """Выбор графика моделью: срок на вызов, не уложилась — повтор, затем TimeoutError."""
    for call in range(1, CHOICE_CALLS + 1):
        try:
            return await asyncio.wait_for(
                llm.complete_structured(SYSTEM_ANALYST, prompt, schema),
                settings.chart_choice_timeout_s,
            )
        except TimeoutError:
            logger.warning(
                "    выбор графика: модель не уложилась в %s с (вызов %d из %d)",
                settings.chart_choice_timeout_s,
                call,
                CHOICE_CALLS,
            )
    raise TimeoutError(f"выбор графика: {CHOICE_CALLS} вызова без ответа")


@dataclass
class _Candidate:
    step: ResearchStep
    table: str
    kind: str
    records: list[dict]
    rows: int


@dataclass
class PickedChart:
    """Выбранный график: таблица (с учётом limit), её строки, вид и колонки."""

    table: str
    records: list[dict]
    chart_type: str
    x: str | None
    y: str | None
    color: str | None
    purpose: str


class ChartPicker:
    """Выбор графика ответа по таблицам шагов; без llm — всегда None."""

    def __init__(self, llm, db) -> None:
        self.llm = llm
        self.db = db

    async def pick(
        self,
        question: str,
        steps: list[ResearchStep],
        tables: dict[int, str],
        final_viz: str = "auto",
    ) -> PickedChart | None:
        """Выбранный график или None; сбой выбора — тоже None, а не провал хода."""
        if self.llm is None:
            return None
        try:
            return await self._pick(question, steps, tables, final_viz)
        except TimeoutError:
            logger.warning("    график ответа: модель не ответила и на повтор — эвристика")
            return None
        except Exception:
            logger.exception("    график ответа: выбор не удался")
            return None

    async def _pick(
        self, question: str, steps: list[ResearchStep], tables: dict[int, str], final_viz: str
    ) -> PickedChart | None:
        candidates = self._candidates(steps, tables)
        if not candidates:
            return None
        if len(candidates) == 1 and candidates[0].rows <= 1:
            # Одно число или одна строка — kpi или таблица, выбирать нечего.
            return None
        by_step = {candidate.step.step_id: candidate for candidate in candidates}
        hint = ANSWER_HINT.format(final_viz=final_viz) if final_viz not in NO_HINT else ""
        described = "\n".join(_describe(candidate) for candidate in candidates)
        why = ""
        for _ in range(PICK_ATTEMPTS):
            prompt = ANSWER_CHART.format(
                question=question,
                hint=hint,
                kinds=CHART_KINDS,
                steps=described,
                retry=ANSWER_RETRY.format(why=why) if why else "",
            )
            answer = await ask_chart(self.llm, prompt, AnswerChart)
            candidate = by_step.get(answer.step_id) if answer.step_id is not None else None
            if candidate is None:
                why = f"шага {answer.step_id} нет среди таблиц — выбери step_id из списка"
            else:
                why = rejection(answer, candidate.records) or ""
            if not why:
                logger.info(
                    "    график ответа: шаг %d %s x=%s y=%s color=%s limit=%s (%s)",
                    answer.step_id,
                    answer.chart_type,
                    answer.x,
                    answer.y,
                    answer.color,
                    answer.limit,
                    answer.reason,
                )
                return self._picked(answer, candidate)
            logger.warning("    график ответа отклонён: %s", why)
        return None

    def _candidates(self, steps: list[ResearchStep], tables: dict[int, str]) -> list[_Candidate]:
        """Таблицы шагов без разведки (lookup) и без повторов одной таблицы."""
        candidates: list[_Candidate] = []
        seen: set[str] = set()
        for step in steps:
            table = tables.get(step.step_id)
            kind = tool_kind(step)
            if table is None or table in seen or kind == "lookup":
                continue
            seen.add(table)
            rows = self.db.query(f"SELECT COUNT(*) AS rows FROM {identifier(table)}")[0]["rows"]
            records = self.db.to_records(table, limit=SPEC_ROW_LIMIT)
            candidates.append(_Candidate(step, table, kind, records, rows))
        return candidates

    def _picked(self, answer: AnswerChart, candidate: _Candidate) -> PickedChart:
        table = top_rows(self.db, candidate.table, answer)
        records = (
            candidate.records
            if table == candidate.table
            else self.db.to_records(table, limit=SPEC_ROW_LIMIT)
        )
        return PickedChart(
            table=table,
            records=records,
            chart_type=answer.chart_type,
            x=answer.x,
            y=answer.y,
            color=answer.color,
            purpose=candidate.step.purpose,
        )


def _describe(candidate: _Candidate) -> str:
    step = candidate.step
    return CHART_STEP.format(
        step_id=step.step_id,
        purpose=step.purpose or step.tool,
        tool=step.tool,
        kind=candidate.kind,
        rows=candidate.rows,
        columns=describe_columns(candidate.records),
        sample=sample_rows(candidate.records),
    )
