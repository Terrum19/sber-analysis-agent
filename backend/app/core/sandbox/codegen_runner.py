"""Раннер кодогенерации: когда подходящего тула нет.

Если на шаге нет тула (tool=None) — LLM генерирует код обработки
данных, который исполняется в WASM-песочнице (см. wasm_runtime.py).
"""

from app.core.data.duckdb_manager import DuckDBManager
from app.core.sandbox.wasm_runtime import WASMSandbox
from app.models.research_step import ResearchStep


async def run_generated_code(step: ResearchStep, input_table: str, db: DuckDBManager) -> str:
    """Сгенерировать и выполнить код шага в песочнице.

    TODO(round-2): запросить код у LLM по описанию step.purpose,
    передать данные input_table в payload, исполнить в WASMSandbox,
    сохранить результат как temp_result_vN.
    """
    sandbox = WASMSandbox()
    payload = {"table": input_table, "data": db.to_records(input_table)}
    result = sandbox.run(code="TODO", payload=payload)  # noqa: F841
    return db.save_step_result(input_table, f"SELECT * FROM {input_table}")
