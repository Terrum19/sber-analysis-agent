"""Исполнитель шагов: последовательно применяет тулы к таблице.

Модель модифицирует одну и ту же таблицу цепочкой запросов;
финальная таблица — основание для инсайта/графика/дашборда.
"""

import logging

from app.core.data.duckdb_manager import DuckDBManager
from app.core.tools.registry import tool_registry
from app.models.research_step import ResearchStep

logger = logging.getLogger(__name__)


class StepExecutor:
    """Исполнение ResearchStep над таблицей в DuckDB."""

    def __init__(self, db: DuckDBManager | None = None) -> None:
        self.db = db or DuckDBManager()

    async def execute(self, step: ResearchStep, input_table: str) -> str:
        """Выполнить один шаг, вернуть имя результирующей temp-таблицы.

        Raises:
            ValueError: если таблица ``input_table`` не существует в DuckDB
                или инструмент ``step.tool`` не зарегистрирован в реестре.
        """
        # Проверяем, что входная таблица существует
        existing = self.db.query(
            "SELECT table_name FROM information_schema.tables WHERE table_name = $1",
            {"1": input_table},
        )
        if not existing:
            msg = f"Таблица '{input_table}' не найдена в базе данных"
            logger.error(msg)
            raise ValueError(msg)

        if step.needs_llm_codegen or not step.tool:
            from app.core.sandbox.codegen_runner import run_generated_code

            return await run_generated_code(step, input_table, self.db)

        try:
            tool = tool_registry.get(step.tool)
        except KeyError:
            msg = f"Инструмент '{step.tool}' не зарегистрирован в реестре"
            logger.error(msg)
            raise ValueError(msg)

        return tool.run(self.db, input_table, step.columns)
