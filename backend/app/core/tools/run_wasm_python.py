"""Bridge from a research step to the existing WASM sandbox."""

from app.config import settings
from app.core.sandbox.wasm_runtime import WASMSandbox
from app.core.tools.base import BaseTool, identifier
from app.core.tools.stats._common import rows_result


class RunWasmPythonTool(BaseTool):
    name = "run_wasm_python"
    description = (
        "Выполнить ограниченный Python-код над target_table; "
        "columns=[python_code, target_table, output_col?]"
    )

    def run(self, db, input_table: str, columns: list[str]) -> str:
        if len(columns) not in (2, 3):
            raise ValueError("run_wasm_python requires [python_code, target_table, output_col?]")
        code, target_table = columns[0], identifier(columns[1])
        output_col = identifier(columns[2]) if len(columns) == 3 else None
        data = db.to_records(target_table, limit=100_000)
        result = WASMSandbox(settings.wasm_timeout_s).run(
            code=code,
            payload={
                "table": target_table,
                "data": data,
                "rows": data,
                "output_col": output_col,
            },
        )
        if not isinstance(result, dict) or "result" not in result:
            raise ValueError("WASM sandbox must return a mapping with a 'result' field")
        result = result["result"]
        if isinstance(result, list) and all(isinstance(row, dict) for row in result):
            if not result:
                raise ValueError("Sandbox returned no rows")
            names = list(result[0])
            if any(set(row) != set(names) for row in result):
                raise ValueError("Sandbox rows must have the same columns")
            rows = [tuple(row[name] for name in names) for row in result]
            return rows_result(db, target_table, names, rows)
        if output_col is None:
            raise ValueError("output_col is required when sandbox returns scalar values")
        values = result if isinstance(result, list) else [result]
        return rows_result(db, target_table, [output_col], [(value,) for value in values])
