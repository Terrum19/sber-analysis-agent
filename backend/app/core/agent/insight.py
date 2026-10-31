"""Генерация финального вывода (инсайта) на бизнес-языке.

Требование UX: ответ читается без пояснений автора — «узнали то,
узнали сё, узнать невозможно» (см. prompts/insight_summary.py).
"""

import json
import logging
import re
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing

from app.config import settings
from app.core.agent.quality_fixes import (
    format_rows,
    insight_facts,
    period_number_facts,
    plan_facts,
    ungrounded_numbers,
)
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.client import LLMClient
from app.core.llm.prompts.insight_summary import (
    INSIGHT_MEMORIES,
    INSIGHT_STREAM_STRUCTURE,
    INSIGHT_STRUCTURE,
    INSIGHT_SUMMARY,
    INSIGHT_SUPPORTING,
    P_VALUE_RULE,
    TILE_INSIGHT,
)
from app.core.llm.prompts.quality_rules import (
    INSIGHT_RULES,
    MANY_ROWS,
    NUMBER_FIX,
    RESULT_SHAPES,
    SINGLE_ROW,
)
from app.core.memory.profile_memory import ProfileMemory
from app.core.tools.base import identifier
from app.models.insight import Insight
from app.models.research_step import ResearchStep

logger = logging.getLogger(__name__)

# Сколько строк результата видит модель.
SHOWN_ROWS = 50
TILE_ROWS = 30


class InsightGenerator:
    """Генератор итогового вывода по результату исследования."""

    def __init__(
        self,
        llm: LLMClient | None = None,
        db: DuckDBManager | None = None,
    ) -> None:
        self.llm = llm or LLMClient()
        self.db = db

    async def generate(
        self,
        question: str,
        result_table: str,
        supporting: list[tuple[str, str]] | None = None,
        steps: list[ResearchStep] | None = None,
        memories: list[str] | None = None,
        result_tool: str = "",
        source: str | None = None,
        inputs: dict[int, str] | None = None,
    ) -> Insight:
        """Сформировать Insight по финальной таблице.

        supporting — (purpose, таблица) результатов стат-тестов и других
        analysis-шагов, которые дополняют основную таблицу. steps —
        выполненные шаги плана: без них «41» в одной строке не знает, что
        это декабрь и сумма больше 150. memories — воспоминания сессии
        («выручка — без НДС»): все включённые, применимость решает модель.
        result_tool — тул шага с результатом: у тренда и сезонности своё
        правило headline (RESULT_SHAPES).
        source — исходная таблица сессии, inputs — шаг -> его входная
        таблица: пропуски и отсечённое лимитом видны только в них.
        """
        prompt = self._prompt(
            INSIGHT_STRUCTURE,
            question,
            result_table,
            supporting,
            steps,
            memories,
            result_tool,
            source,
            inputs,
        )
        if not settings.agent_quality_fixes:
            return await self.llm.complete_structured("Ты — аналитик.", prompt, Insight)
        # Опора — весь промпт: условия шагов плана («> 150», «2024»), воспоминания, порог 0.05.
        return await self._grounded(prompt)

    async def stream(
        self,
        question: str,
        result_table: str,
        supporting: list[tuple[str, str]] | None = None,
        steps: list[ResearchStep] | None = None,
        memories: list[str] | None = None,
        result_tool: str = "",
        source: str | None = None,
        inputs: dict[int, str] | None = None,
        *,
        emit: Callable[[str], None],
    ) -> tuple[Insight, bool]:
        """Вывод текстом по кусочкам: куски уходят в emit по мере ответа.

        Промпт тот же, что у generate(), меняется только формат ответа
        (Markdown вместо полей схемы). Возвращает Insight, разобранный из
        текста, и признак «текст показан». Не показан (у клиента нет
        стрима, стрим упал до первого куска или пуст) — вывод строит
        generate().

        H-Q3 как у generate(): первая строка (headline) придерживается, пока
        не дописана. Есть в ней число без опоры в промпте — стрим
        закрывается и идёт один переспрос с NUMBER_FIX, пользователь первого
        ответа не видит. Остальные строки идут без задержки.
        """
        complete_stream = getattr(self.llm, "complete_stream", None)
        args = (question, result_table, supporting, steps, memories, result_tool, source, inputs)
        if not callable(complete_stream):
            return await self.generate(*args), False
        prompt = self._prompt(INSIGHT_STREAM_STRUCTURE, *args)
        check = settings.agent_quality_fixes
        text, missing = await self._stream_once(complete_stream, prompt, emit, check, args)
        if missing:
            logger.warning("инсайт (стрим): числа без опоры в фактах %s, переспрос", missing)
            fix = NUMBER_FIX.format(numbers=", ".join(missing))
            text, _ = await self._stream_once(complete_stream, prompt + fix, emit, False, args)
        if text is None:
            logger.warning("инсайт: стрим не дал текста, structured-вывод")
            return await self.generate(*args), False
        insight = parse_insight_text(text)
        if not insight.headline:
            # Модель начала сразу со списка: headline — первый пункт, иначе начало текста.
            insight.headline = insight.details.pop(0) if insight.details else _plain(text)[:300]
        return insight, True

    async def _stream_once(
        self,
        complete_stream: Callable[..., AsyncIterator[str]],
        prompt: str,
        emit: Callable[[str], None],
        check: bool,
        args: tuple,
    ) -> tuple[str | None, list[str]]:
        """Один стрим: (показанный текст или None, если ничего не показано; числа без опоры).

        check — придержать первую строку и проверить её числа (H-Q3). Нашлись
        числа без опоры — стрим закрывается, текст не показан. Ошибка до
        первого показанного куска — тоже «не показано», после — наверх:
        второй ответ дал бы дубль.
        """
        held: list[str] = []
        shown: list[str] = []

        def release() -> list[str]:
            """Проверить придержанное; без чисел без опоры — показать его."""
            nonlocal check
            text = "".join(held)
            first = parse_insight_text(text.strip().split("\n", 1)[0])
            missing = ungrounded_numbers(first.headline or "".join(first.details[:1]), prompt)
            if not missing:
                check = False
                shown.extend(held)
                for chunk in held:
                    emit(chunk)
            return missing

        try:
            async with aclosing(complete_stream("Ты — аналитик.", prompt)) as deltas:
                async for delta in deltas:
                    if not check:
                        shown.append(delta)
                        emit(delta)
                        continue
                    held.append(delta)
                    # Первая строка дописана, когда после текста пришёл перевод строки.
                    if "\n" in "".join(held).lstrip() and (missing := release()):
                        return None, missing
            if check and held and (missing := release()):
                return None, missing
        except Exception:
            if shown:
                raise
            logger.warning("инсайт: стрим упал до показа текста", exc_info=True)
            return None, []
        text = "".join(shown)
        return (text if text.strip() else None), []

    def _prompt(
        self,
        structure: str,
        question: str,
        result_table: str,
        supporting: list[tuple[str, str]] | None,
        steps: list[ResearchStep] | None,
        memories: list[str] | None,
        result_tool: str,
        source: str | None,
        inputs: dict[int, str] | None,
    ) -> str:
        """Промпт вывода: вопрос, результат, сопутствующие расчёты, факты и правила."""
        from app.core.data.duckdb_manager import DuckDBManager

        db = self.db or DuckDBManager()
        result = db.to_records(result_table, limit=SHOWN_ROWS)
        supporting = [
            (purpose, db.to_records(table, limit=10)) for purpose, table in supporting or []
        ]
        notes = describe_memories(memories or [])
        tested = has_p_value([result, *(rows for _, rows in supporting)])
        if not settings.agent_quality_fixes:
            extra = "\n".join(f"- {purpose}: {rows}" for purpose, rows in supporting)
            prompt = INSIGHT_SUMMARY.format(question=question, result=result, structure=structure)
            prompt += describe_supporting(extra, tested)
            return prompt + notes
        # Строки — компактной таблицей с ISO-датами: repr datetime мешает модели читать ряд.
        shown = format_rows(result)
        extra = "\n".join(f"- {purpose}:\n{format_rows(rows)}" for purpose, rows in supporting)
        prompt = INSIGHT_SUMMARY.format(question=question, result=shown, structure=structure)
        prompt += describe_supporting(extra, tested)
        prompt += notes
        try:
            facts = insight_facts(db, result_table, shown=SHOWN_ROWS)
        except Exception:
            # Факты — подсказка, а не условие ответа: без них инсайт строится как раньше.
            logger.warning("инсайт: факты по %s не посчитались", result_table, exc_info=True)
            facts = ""
        try:
            profile = ProfileMemory(db).load_profile(source) if source else None
            columns = list(result[0]) if result else []
            more, hints = plan_facts(db, steps or [], inputs or {}, profile, columns)
        except Exception:
            logger.warning("инсайт: факты по плану не посчитались", exc_info=True)
            more, hints = [], []
        more += period_number_facts(steps or [])
        facts = "\n".join(line for line in [facts, *more] if line) or "нет"
        # Одна строка — ответ числом; ряд — лидер, минимум или число строк, а не одно значение.
        # Число строк всей таблицы, а не показанных модели (их не больше SHOWN_ROWS).
        rows = db.query(f"SELECT COUNT(*) AS n FROM {identifier(result_table)}")[0]["n"]
        shape = SINGLE_ROW if rows <= 1 else MANY_ROWS.format(rows=rows)
        shape = RESULT_SHAPES.get(result_tool, shape)
        plan = describe_steps(steps or [])
        prompt += INSIGHT_RULES.format(plan=plan, facts=facts, shape=shape, hints="".join(hints))
        return prompt

    async def generate_tile_insight(self, title: str, result_table: str, chart_type: str) -> str:
        from app.core.data.duckdb_manager import DuckDBManager

        db = self.db or DuckDBManager()
        result = db.to_records(result_table, limit=TILE_ROWS)
        prompt = TILE_INSIGHT.format(title=title, chart_type=chart_type, result=result)
        text = await self.llm.complete("Ты — аналитик.", prompt)
        return text.strip()

    async def _grounded(self, prompt: str) -> Insight:
        """H-Q3: число в headline, которого нет в промпте, — один переспрос."""
        insight = await self.llm.complete_structured("Ты — аналитик.", prompt, Insight)
        missing = ungrounded_numbers(insight.headline, prompt)
        if not missing:
            return insight
        logger.warning("инсайт: числа без опоры в фактах %s, переспрос", missing)
        fix = NUMBER_FIX.format(numbers=", ".join(missing))
        return await self.llm.complete_structured("Ты — аналитик.", prompt + fix, Insight)


# «Ограничения: …» / «Ограничение — …», в т.ч. после маркера списка.
_LIMITS_RE = re.compile(r"^ограничени[ея]\s*[:\-—]\s*(.*)$", re.IGNORECASE)
_BULLET_RE = re.compile(r"^(?:[-–—*•]|\d+[.)])\s+(.*)$")
_HEADLINE_LABEL_RE = re.compile(r"^(?:headline|вывод)\s*:\s*", re.IGNORECASE)
_NO_LIMITS = {"нет", "нет.", "-", "—"}


def _plain(line: str) -> str:
    """Строка без Markdown-разметки: заголовок, **, __, `."""
    line = re.sub(r"^#+\s*", "", line.strip())
    return line.replace("**", "").replace("__", "").replace("`", "").strip()


def parse_insight_text(text: str) -> Insight:
    """Текст стрима -> Insight: для журнала хода, PDF и «Спросить об этом».

    headline — первая строка без маркера, details — строки списка (продолжение
    без маркера приклеивается к последнему пункту), limits — после
    «Ограничения:». Разметка снимается: в Insight — простой текст.
    """
    text = re.sub(r"^```\w*\s*|\s*```$", "", text.strip())
    headline, details, limits = "", [], []
    in_limits = False
    for raw in text.splitlines():
        line = _plain(raw)
        if not line:
            continue
        bullet = _BULLET_RE.match(line)
        content = bullet.group(1).strip() if bullet else line
        if in_limits:
            limits.append(content)
        elif limit := _LIMITS_RE.match(content):
            in_limits = True
            limits.append(limit.group(1).strip())
        elif not headline and not bullet:
            headline = _HEADLINE_LABEL_RE.sub("", content)
        elif bullet or not details:
            details.append(content)
        else:
            details[-1] = f"{details[-1]} {content}"
    joined = " ".join(item for item in limits if item)
    return Insight(
        headline=headline,
        details=details,
        limits="" if joined.lower() in _NO_LIMITS else joined,
    )


def describe_steps(steps: list[ResearchStep]) -> str:
    """Шаги плана для вывода: «1. purpose — tool {аргументы}»."""
    lines = [
        f"{number}. {step.purpose or step.tool} — {step.tool} "
        f"{json.dumps(step.arguments or step.columns, ensure_ascii=False, default=str)}"
        for number, step in enumerate(steps, 1)
    ]
    return "\n".join(lines) or "нет"


def has_p_value(tables: list[list[dict]]) -> bool:
    """Есть ли колонка p_value хотя бы в одной таблице: только тогда нужен порог 0.05."""
    return any("p_value" in (key.lower() for key in rows[0]) for rows in tables if rows)


def describe_supporting(extra: str, tested: bool) -> str:
    """Сопутствующие результаты — когда они есть, порог p_value — когда есть тест."""
    text = INSIGHT_SUPPORTING.format(supporting=extra) if extra else ""
    return text + (P_VALUE_RULE if tested else "")


def describe_memories(memories: list[str]) -> str:
    """Блок воспоминаний для вывода; без воспоминаний — пусто."""
    if not memories:
        return ""
    return INSIGHT_MEMORIES.format(memories="\n".join(f"- {text}" for text in memories))
