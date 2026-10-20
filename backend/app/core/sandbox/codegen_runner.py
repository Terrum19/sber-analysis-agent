"""Раннер кодогенерации: LLM пишет Python-код, песочница его исполняет.

Используется, когда для шага плана нет подходящего тула
(needs_llm_codegen=true или tool=null). Входная таблица выгружается
в папку запуска целиком (input.csv), результат (result.csv) сохраняется
в DuckDB как temp_result_vN.
"""

import asyncio
import json
import re

from app.config import settings
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.client import LLMClient
from app.core.llm.prompts.codegen import CODEGEN, CODEGEN_FIX
from app.core.llm.prompts.system_analyst import SYSTEM_ANALYST
from app.core.sandbox.wasm_runtime import INPUT_FILE, RESULT_FILE, SandboxError, WASMSandbox
from app.models.research_step import ResearchStep

SAMPLE_ROWS = 5
DEFAULT_PURPOSE = "Описательная статистика по всем колонкам таблицы"
_FENCE = re.compile(r"```(?:python|py)?[^\n]*\n(.*?)```", re.DOTALL)


def extract_code(text: str) -> str:
    """Вырезать код из ответа LLM, даже если он обёрнут в ```python ... ```."""
    blocks = _FENCE.findall(text)
    return (max(blocks, key=len) if blocks else text).strip()


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_prompt(step: ResearchStep, input_table: str, db: DuckDBManager) -> str:
    """Промпт по цели шага, схеме и первым строкам входной таблицы."""
    schema = db.query(f"DESCRIBE {input_table}")
    sample = db.to_records(input_table, limit=SAMPLE_ROWS)
    return CODEGEN.format(
        purpose=step.purpose or DEFAULT_PURPOSE,
        columns=step.columns or "все",
        schema=json.dumps(
            {row["column_name"]: row["column_type"] for row in schema}, ensure_ascii=False
        ),
        sample=json.dumps(sample, ensure_ascii=False, default=str),
    )


async def run_generated_code(
    step: ResearchStep,
    input_table: str,
    db: DuckDBManager,
    llm: LLMClient | None = None,
    sandbox: WASMSandbox | None = None,
) -> str:
    """Сгенерировать и выполнить код шага в песочнице, вернуть temp_result_vN.

    Если код падает, LLM получает текст ошибки и пишет исправление
    (всего settings.codegen_attempts попыток); последняя ошибка поднимается
    как SandboxError.
    """
    llm = llm or LLMClient()
    sandbox = sandbox or WASMSandbox()
    prompt = build_prompt(step, input_table, db)
    with sandbox.workspace() as workdir:
        with db.lock:
            db.execute(
                f"COPY (SELECT * FROM {input_table}) TO "
                f"{sql_literal(str(workdir / INPUT_FILE))} (HEADER)"
            )
        error: SandboxError | None = None
        for _ in range(max(1, settings.codegen_attempts)):
            code = extract_code(await llm.complete(SYSTEM_ANALYST, prompt))
            try:
                await asyncio.to_thread(sandbox.run, code, workdir)
            except SandboxError as exc:
                error = exc
                prompt = CODEGEN_FIX.format(error=str(exc)[-2000:], code=code)
                continue
            result = sql_literal(str(workdir / RESULT_FILE))
            return db.save_step_result(input_table, f"SELECT * FROM read_csv_auto({result})")
    raise error
