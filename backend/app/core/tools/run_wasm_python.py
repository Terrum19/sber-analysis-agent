"""Bridge from a research step to the existing WASM sandbox."""

from app.core.sandbox.codegen_runner import sql_literal
from app.core.sandbox.wasm_runtime import INPUT_FILE, RESULT_FILE, WASMSandbox
from app.core.tools.base import BaseTool, Ident, TableRef, ToolArgs, identifier

SCALAR_WRAP = """
if not hasattr(result, "columns") and not (
    isinstance(result, list) and result and all(isinstance(row, dict) for row in result)
):
    result = {{{output_col!r}: result if isinstance(result, list) else [result]}}
"""


class RunWasmPythonArgs(ToolArgs):
    python_code: str
    target_table: TableRef
    output_col: Ident | None = None


class RunWasmPythonTool(BaseTool):
    name = "run_wasm_python"
    description = "Выполнить ограниченный Python-код над target_table"
    Args = RunWasmPythonArgs

    def execute(self, db, input_table: str, args: RunWasmPythonArgs) -> str:
        code = args.python_code
        if args.output_col is not None:
            code += SCALAR_WRAP.format(output_col=args.output_col)
        sandbox = WASMSandbox()
        with sandbox.workspace() as workdir:
            with db.lock:
                db.execute(
                    f"COPY (SELECT * FROM {identifier(args.target_table)}) TO "
                    f"{sql_literal(str(workdir / INPUT_FILE))} (HEADER)"
                )
            sandbox.run(code, workdir)
            result = sql_literal(str(workdir / RESULT_FILE))
            return db.save_step_result(
                args.target_table,
                f"SELECT * FROM read_csv_auto({result})",
                code=args.python_code,
                language="python",
            )
