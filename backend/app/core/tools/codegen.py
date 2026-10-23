"""Тул run_python: Python-код в WASM-песочнице для задач, которые тулы не покрывают.

Код пишет не планировщик, а отдельный вызов LLM по purpose, схеме и
первым строкам входной таблицы (sandbox/codegen_runner.py); ошибки кода
исправляются там же, в пределах settings.codegen_attempts.
"""

from functools import cache
from typing import Any, Literal

from pydantic import Field

from app.config import settings
from app.core.sandbox import codegen_runner
from app.core.sandbox.wasm_runtime import WASMSandbox
from app.core.tools.base import BaseTool, ToolArgs, ToolContext


@cache
def sandbox_available() -> bool:
    """Установлены ли node и Pyodide (проверяется один раз на процесс)."""
    return WASMSandbox().available()


class CodegenArgs(ToolArgs):
    purpose: str = Field(min_length=20)
    output: Literal["table", "scalar"] = "table"


class CodegenTool(BaseTool):
    name = "run_python"
    description = (
        "Python-код в песочнице (pandas, numpy, scipy, statsmodels), когда ни один "
        "инструмент не подходит; не используй, если задача решается инструментами. "
        "purpose — подробно словами, что посчитать (по нему пишется код); "
        "output=scalar — если ответ одно значение"
    )
    kind = "transform"
    Args = CodegenArgs

    def enabled(self) -> bool:
        return settings.codegen_enabled and sandbox_available()

    async def arun(
        self,
        ctx: ToolContext,
        db,
        input_table: str,
        columns: list[Any] | None = None,
        **arguments: Any,
    ) -> str:
        args = self.parse_args(columns, arguments)
        return await codegen_runner.run_generated_code(
            args.purpose, input_table, db, ctx.llm, ctx.sandbox, output=args.output
        )

    def execute(self, db, input_table: str, args: CodegenArgs) -> str:
        raise TypeError("run_python исполняется только через arun: ему нужны LLM и песочница")
