"""Исполнитель шагов: последовательно применяет тулы к таблице.

Модель модифицирует одну и ту же таблицу цепочкой запросов;
финальная таблица — основание для инсайта/графика/дашборда.
"""

from app.core.data.duckdb_manager import DuckDBManager
from app.core.tools.registry import tool_registry
from app.models.research_step import ResearchStep


class StepExecutor:
    """Исполнение ResearchStep над таблицей в DuckDB."""

    def __init__(self, db: DuckDBManager | None = None) -> None:
        self.db = db or DuckDBManager()

    async def execute(self, step: ResearchStep, input_table: str) -> str:
        """Выполнить один шаг, вернуть имя результирующей temp-таблицы."""
        if step.needs_llm_codegen or not step.tool:
            from app.core.sandbox.codegen_runner import run_generated_code

            return await run_generated_code(step, input_table, self.db)
        tool = tool_registry.get(step.tool)
        return tool.run(self.db, input_table, step.columns, **step.parameters)


def transforms_data(step: ResearchStep) -> bool:
    """Whether the step's result replaces the working table for the next step.

    Report tools (statistical tests, value search, table inspection) return a
    small summary; the next step keeps working on the table they analysed.
    Generated code and unknown tools are treated as transformations.
    """
    if step.needs_llm_codegen or not step.tool:
        return True
    try:
        tool = tool_registry.get(step.tool)
    except KeyError:
        return True
    return getattr(tool, "transforms_data", True)
