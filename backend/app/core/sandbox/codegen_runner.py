"""Раннер кодогенерации: LLM пишет Python-код, песочница его исполняет.

Вызывается тулом run_python (core/tools/codegen.py), который планировщик
выбирает явно. Входная таблица выгружается в папку запуска целиком
(input.csv, не больше settings.codegen_max_rows строк), результат
(result.csv) сохраняется в DuckDB как temp_result_vN.
"""

import asyncio
import json
import re
from typing import Literal

from app.config import settings
from app.core.data.duckdb_manager import DuckDBManager
from app.core.llm.client import LLMClient
from app.core.llm.prompts.codegen import CODEGEN, CODEGEN_FIX, OUTPUT_HINTS
from app.core.llm.prompts.system_analyst import SYSTEM_ANALYST
from app.core.sandbox.wasm_runtime import INPUT_FILE, RESULT_FILE, SandboxError, WASMSandbox

SAMPLE_ROWS = 5
_FENCE = re.compile(r"```(?:python|py)?[^\n]*\n(.*?)```", re.DOTALL)

# Одно значение (число, строка, numpy-скаляр) -> таблица из одной ячейки.
SCALAR_WRAP = """
if not isinstance(result, (dict, list)) and not hasattr(result, "columns") \\
        and not hasattr(result, "to_frame"):
    result = {"value": [result]}
"""

Output = Literal["table", "scalar"]


def extract_code(text: str) -> str:
    """Вырезать код из ответа LLM, даже если он обёрнут в ```python ... ```."""
    blocks = _FENCE.findall(text)
    return (max(blocks, key=len) if blocks else text).strip()


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_prompt(purpose: str, input_table: str, db: DuckDBManager, output: Output) -> str:
    """Промпт по цели шага, схеме и первым строкам входной таблицы."""
    schema = db.query(f"DESCRIBE {input_table}")
    sample = db.to_records(input_table, limit=SAMPLE_ROWS)
    return CODEGEN.format(
        purpose=purpose,
        output=OUTPUT_HINTS[output],
        schema=json.dumps(
            {row["column_name"]: row["column_type"] for row in schema}, ensure_ascii=False
        ),
        sample=json.dumps(sample, ensure_ascii=False, default=str),
    )


async def run_generated_code(
    purpose: str,
    input_table: str,
    db: DuckDBManager,
    llm: LLMClient | None = None,
    sandbox: WASMSandbox | None = None,
    output: Output = "table",
) -> str:
    """Сгенерировать и выполнить код по purpose в песочнице, вернуть temp_result_vN.

    Если код падает, LLM получает текст ошибки и пишет исправление
    (всего settings.codegen_attempts попыток); последняя ошибка поднимается
    как SandboxError.

    Raises:
        ValueError: во входной таблице больше settings.codegen_max_rows строк.
        SandboxError: код так и не отработал.
    """
    rows = db.query(f"SELECT count(*) AS n FROM {input_table}")[0]["n"]
    if rows > settings.codegen_max_rows:
        raise ValueError(
            f"run_python: во входной таблице {rows} строк, лимит песочницы "
            f"{settings.codegen_max_rows}; сначала отфильтруй, сгруппируй или возьми sample"
        )
    llm = llm or LLMClient()
    sandbox = sandbox or WASMSandbox()
    prompt = build_prompt(purpose, input_table, db, output)
    with sandbox.workspace() as workdir:
        with db.lock:
            db.execute(
                f"COPY (SELECT * FROM {input_table}) TO "
                f"{sql_literal(str(workdir / INPUT_FILE))} (HEADER)"
            )
        error: SandboxError | None = None
        for _ in range(max(1, settings.codegen_attempts)):
            code = extract_code(await llm.complete(SYSTEM_ANALYST, prompt))
            wrapped = code + SCALAR_WRAP if output == "scalar" else code
            try:
                await asyncio.to_thread(sandbox.run, wrapped, workdir)
            except SandboxError as exc:
                error = exc
                prompt = CODEGEN_FIX.format(error=str(exc)[-2000:], code=code)
                continue
            result = sql_literal(str(workdir / RESULT_FILE))
            return db.save_step_result(
                input_table,
                f"SELECT * FROM read_csv_auto({result})",
                code=code,
                language="python",
            )
    raise error
