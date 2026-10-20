"""Bridge from a research step to the existing WASM sandbox."""

from app.core.sandbox.codegen_runner import sql_literal
from app.core.sandbox.wasm_runtime import INPUT_FILE, RESULT_FILE, WASMSandbox
from app.core.tools.base import BaseTool, identifier

SCALAR_WRAP = """
if not hasattr(result, "columns") and not (
    isinstance(result, list) and result and all(isinstance(row, dict) for row in result)
):
    result = {{{output_col!r}: result if isinstance(result, list) else [result]}}
"""


class RunWasmPythonTool(BaseTool):
    name = "run_wasm_python"
    description = (
        "Выполнить ограниченный Python-код над target_table; "
        "columns=[python_code, target_table, output_col?]"
    )

    def run(self, db, input_table: str, columns: list[str], **kwargs: object) -> str:
        if len(columns) not in (2, 3):
            raise ValueError("run_wasm_python requires [python_code, target_table, output_col?]")
        code, target_table = columns[0], columns[1]
        output_col = columns[2] if len(columns) == 3 else None
        if output_col is not None:
            identifier(output_col)
            code += SCALAR_WRAP.format(output_col=output_col)
        source = identifier(target_table)
        sandbox = WASMSandbox()
        with sandbox.workspace() as workdir:
            with db.lock:
                db.execute(
                    f"COPY (SELECT * FROM {source}) TO "
                    f"{sql_literal(str(workdir / INPUT_FILE))} (HEADER)"
                )
            sandbox.run(code, workdir)
            result = sql_literal(str(workdir / RESULT_FILE))
            return db.save_step_result(target_table, f"SELECT * FROM read_csv_auto({result})")
