"""Исполнитель шагов: применяет тулы к таблицам в DuckDB.

По умолчанию шаг берёт результат последнего transform-шага ($prev);
шаг может сослаться на исходную таблицу ($source) или на результат
любого выполненного шага ($step_N) — и во входе, и в аргументах.
Финальная таблица — основание для инсайта/графика/дашборда.
"""

import logging
import re
from typing import Any

from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.client import LLMClient
from app.core.sandbox.wasm_runtime import WASMSandbox
from app.core.tools.base import ToolKind
from app.core.tools.registry import tool_registry
from app.models.research_step import PREVIOUS, SOURCE, ResearchStep

logger = logging.getLogger(__name__)

_STEP_REF = re.compile(r"^\$step_(\d+)$")


def tool_kind(step: ResearchStep) -> ToolKind:
    """Вид шага: transform продолжает конвейер, analysis/lookup — нет."""
    if step.needs_llm_codegen or not step.tool:
        return "transform"
    try:
        return tool_registry.get(step.tool).kind
    except KeyError:
        return "transform"


class StepExecutor:
    """Исполнение ResearchStep над таблицей в DuckDB."""

    def __init__(
        self,
        db: DuckDBManager | None = None,
        llm: LLMClient | None = None,
        sandbox: WASMSandbox | None = None,
    ) -> None:
        self.db = db or DuckDBManager()
        self.llm = llm
        self.sandbox = sandbox

    async def execute(
        self,
        step: ResearchStep,
        input_table: str,
        step_tables: dict[int, str] | None = None,
        source_table: str | None = None,
    ) -> str:
        """Выполнить один шаг, вернуть имя результирующей temp-таблицы.

        Args:
            input_table: таблица для ссылки $prev.
            step_tables: результаты уже выполненных шагов (для $step_N).
            source_table: исходная таблица (для $source), по умолчанию input_table.

        Raises:
            ValueError: если входная таблица не существует, ссылка указывает
                на невыполненный шаг или инструмент не зарегистрирован.
        """
        refs = {PREVIOUS: input_table, SOURCE: source_table or input_table}
        resolve = _Resolver(refs, step_tables or {})
        table = resolve.table(step.input)

        existing = self.db.query(
            "SELECT table_name FROM information_schema.tables WHERE table_name = $tbl",
            {"tbl": table},
        )
        if not existing:
            msg = f"Таблица '{table}' не найдена в базе данных"
            logger.error(msg)
            raise ValueError(msg)

        if step.needs_llm_codegen or not step.tool:
            from app.core.sandbox.codegen_runner import run_generated_code

            return await run_generated_code(step, table, self.db, self.llm, self.sandbox)

        logger.info(
            ">>> StepExecutor.execute() шаг purpose=%r tool=%s args=%s columns=%s input_table=%s",
            step.purpose,
            step.tool,
            step.arguments,
            step.columns,
            table,
        )
        try:
            tool = tool_registry.get(step.tool)
        except KeyError:
            msg = f"Инструмент '{step.tool}' не зарегистрирован в реестре"
            logger.error(msg)
            raise ValueError(msg)

        result = tool.run(
            self.db, table, resolve.value(step.columns), **resolve.value(step.arguments)
        )
        logger.info("<<< StepExecutor.execute() УСПЕШНО result_table=%s", result)
        return result


class _Resolver:
    """Подстановка ссылок $prev/$source/$step_N на имена temp-таблиц."""

    def __init__(self, refs: dict[str, str], step_tables: dict[int, str]) -> None:
        self.refs, self.step_tables = refs, step_tables

    def table(self, ref: str) -> str:
        # Во входе допускаем и «step_N» без $ — колонкой это быть не может.
        if re.fullmatch(r"step_\d+", ref):
            ref = f"${ref}"
        return self.value(ref)

    def value(self, value: Any) -> Any:
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, dict):
            return {key: self.value(item) for key, item in value.items()}
        if not isinstance(value, str):
            return value
        if value in self.refs:
            return self.refs[value]
        match = _STEP_REF.fullmatch(value)
        if match is None:
            return value
        step_id = int(match.group(1))
        if step_id not in self.step_tables:
            raise ValueError(f"Ссылка {value}: шаг {step_id} ещё не выполнен")
        return self.step_tables[step_id]
