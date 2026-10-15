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
        return tool.run(self.db, input_table, step.columns)
