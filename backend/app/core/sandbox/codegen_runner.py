"""Раннер кодогенерации: когда подходящего тула нет.

Если на шаге нет тула (tool=None) — LLM генерирует код обработки
данных, который исполняется в WASM-песочнице (см. wasm_runtime.py).
"""

from app.core.data.duckdb_manager import DuckDBManager
from app.models.research_step import ResearchStep


async def run_generated_code(step: ResearchStep, input_table: str, db: DuckDBManager) -> str:
    """Сгенерировать и выполнить код шага в песочнице.

    TODO(round-2): запросить код у LLM по описанию step.purpose,
    передать данные input_table в payload, исполнить в WASMSandbox,
    сохранить результат как temp_result_vN.

    Пока не реализовано — падаем явно, а не возвращаем копию входной
    таблицы: иначе инсайт строится по данным, которые никто не считал.
    """
    raise NotImplementedError(
        f"Шаг {step.step_id} требует генерации кода ({step.purpose or 'без описания'}), "
        "но кодогенерация пока не поддерживается. Подходящего инструмента нет."
    )
