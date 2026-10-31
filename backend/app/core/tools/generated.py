"""Адаптеры декларативной спеки в обычные BaseTool.

``GeneratedSqlTool`` подставляет типизированные аргументы в проверенный
SQL-шаблон и сохраняет результат как temp_result_vN. ``GeneratedPythonTool``
исполняет фиксированный код только в WASM-песочнице. Ни один из них не
импортирует сгенерированный Python в процесс бэкенда.
"""

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from pydantic import Field, create_model

from app.config import settings
from app.core.tools.base import BaseTool, Ident, ToolArgs, ToolContext, identifier, literal
from app.models.generated_tool import GeneratedToolSpec, ToolParameterSpec

_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def build_args_model(spec: GeneratedToolSpec) -> type[ToolArgs]:
    """Собрать Pydantic-модель аргументов из объявленных параметров."""
    fields: dict[str, tuple[Any, Any]] = {}
    for parameter in spec.parameters:
        annotation = _annotation(parameter)
        if parameter.required:
            default: Any = ...
        else:
            annotation = annotation | None
            default = parameter.default
        fields[parameter.name] = (
            annotation,
            Field(default, description=parameter.description or None),
        )
    return create_model(  # type: ignore[call-overload]
        f"Generated_{spec.name}_Args",
        __base__=ToolArgs,
        **fields,
    )


def _annotation(parameter: ToolParameterSpec) -> Any:
    if parameter.type == "column":
        return Ident
    if parameter.type == "columns":
        return list[Ident]
    if parameter.type == "integer":
        return int
    if parameter.type == "number":
        return float
    if parameter.type == "boolean":
        return bool
    if parameter.type == "enum":
        return _enum_literal(parameter.values)
    return str


def _enum_literal(values: list[str]) -> Any:
    from typing import Literal

    return Literal[tuple(values)]  # type: ignore[valid-type]


def build_tool(spec: GeneratedToolSpec) -> BaseTool:
    """Создать адаптер по спецификации (без регистрации и проверок)."""
    if spec.language == "python":
        return GeneratedPythonTool(spec)
    return GeneratedSqlTool(spec)


def sample_values(spec: GeneratedToolSpec) -> dict[str, Any]:
    """Аргументы для smoke-проверки: примеры из спеки + значения по умолчанию."""
    values = {argument.name: argument.value for argument in spec.sample_arguments}
    for parameter in spec.parameters:
        if parameter.name not in values and parameter.default is not None:
            values[parameter.name] = parameter.default
    return values


def render_parameter(parameter: ToolParameterSpec, value: Any) -> str:
    """Значение аргумента -> безопасный SQL-фрагмент."""
    if value is None:
        if parameter.required:
            raise ValueError(f"{parameter.name}: a value is required")
        return "NULL"
    if parameter.type == "columns":
        if not isinstance(value, (list, tuple)) or not value:
            raise ValueError(f"{parameter.name}: expected a non-empty list of columns")
        return ", ".join(identifier(str(item)) for item in value)
    if parameter.type == "column":
        if not isinstance(value, str):
            raise ValueError(f"{parameter.name}: expected a column name")
        return identifier(value)
    if parameter.type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{parameter.name}: expected an integer")
        return literal(value)
    if parameter.type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{parameter.name}: expected a number")
        return literal(float(value))
    if parameter.type == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{parameter.name}: expected a boolean")
        return literal(value)
    if parameter.type == "enum":
        if str(value) not in parameter.values:
            raise ValueError(f"{parameter.name}: expected one of {parameter.values}")
        return literal(str(value))
    if value is None:
        return "NULL"
    return literal(str(value))


def render_template(template: str, values: dict[str, str]) -> str:
    """Подставить {placeholder}-ы, не используя str.format (его спецсимволы)."""

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise ValueError(f"unknown SQL placeholder {{{name}}}")
        return values[name]

    stripped = _PLACEHOLDER.sub("", template)
    if "{" in stripped or "}" in stripped:
        raise ValueError("invalid SQL placeholder syntax")
    return _PLACEHOLDER.sub(replace, template)


def render_sql(spec: GeneratedToolSpec, input_table: str, values: dict[str, Any]) -> str:
    """Отрендерить SQL-шаблон спеки (input + объявленные параметры)."""
    if not spec.sql:
        raise ValueError(f"{spec.name}: SQL tool has no sql")
    substitutions = {"input": identifier(input_table)}
    for parameter in spec.parameters:
        if parameter.name in values:
            substitutions[parameter.name] = render_parameter(parameter, values[parameter.name])
        elif parameter.default is not None:
            substitutions[parameter.name] = render_parameter(parameter, parameter.default)
        else:
            substitutions[parameter.name] = "NULL"
    return render_template(spec.sql, substitutions)


def wrap_python_code(code: str, values: dict[str, Any]) -> str:
    """Код инструмента + params: JSON -> Python-литерал, без импортов."""
    params = json.loads(json.dumps(values, ensure_ascii=False, default=str))
    return f"params = {params!r}\n{code}\n"


def _validate_rendered(sql: str, input_table: str) -> None:
    """Повторная статическая проверка уже отрендеренного SQL (анти-подмена)."""
    from app.core.tools.toolgen.validator import check_rendered_sql

    check_rendered_sql(sql, allowed_tables={input_table})


class GeneratedSqlTool(BaseTool):
    """Проверенный SQL-инструмент из декларативной спеки."""

    def __init__(self, spec: GeneratedToolSpec) -> None:
        self.spec = spec
        self.name = spec.name
        self.description = spec.description
        self.kind = spec.kind
        self.Args = build_args_model(spec)

    def enabled(self) -> bool:
        return settings.toolgen_enabled

    def execute(self, db, input_table: str, args: ToolArgs) -> str:
        values = args.model_dump()
        sql = render_sql(self.spec, input_table, values)
        _validate_rendered(sql, input_table)
        return db.save_step_result(input_table, sql)


class GeneratedPythonTool(BaseTool):
    """Проверенный Python-инструмент: код исполняется только в WASM-песочнице."""

    def __init__(self, spec: GeneratedToolSpec) -> None:
        self.spec = spec
        self.name = spec.name
        self.description = spec.description
        self.kind = spec.kind
        self.Args = build_args_model(spec)

    def enabled(self) -> bool:
        from app.core.tools.codegen import sandbox_available

        return settings.toolgen_enabled and settings.codegen_enabled and sandbox_available()

    async def arun(
        self,
        ctx: ToolContext,
        db,
        input_table: str,
        columns: list[Any] | None = None,
        /,
        **arguments: Any,
    ) -> str:
        from app.core.sandbox.wasm_runtime import RESULT_FILE, SandboxError, WASMSandbox

        args = self.parse_args(columns, arguments)
        sandbox = ctx.sandbox or WASMSandbox()
        if not sandbox.available():
            raise SandboxError("WASM sandbox is not available")
        if not self.spec.code:
            raise ValueError(f"{self.name}: Python tool has no code")
        # Код мог быть подменён после регистрации: проверяем его снова.
        from app.core.tools.toolgen.validator import check_python_code

        check_python_code(self.spec.code, [parameter.name for parameter in self.spec.parameters])
        rows = db.query(f"SELECT COUNT(*) AS n FROM {identifier(input_table)}")[0]["n"]
        if rows > settings.codegen_max_rows:
            raise ValueError(
                f"{self.name}: во входной таблице {rows} строк, лимит песочницы "
                f"{settings.codegen_max_rows}; сначала отфильтруй или сгруппируй"
            )
        code = self._wrapped_code(args.model_dump())
        with sandbox.workspace() as workdir:
            input_path = workdir / "input.csv"
            with db.lock:
                db.execute(
                    f"COPY (SELECT * FROM {identifier(input_table)} LIMIT "
                    f"{settings.codegen_max_rows}) TO {literal(str(input_path))} (HEADER)"
                )
            await asyncio.to_thread(sandbox.run, code, workdir)
            result_path = Path(workdir) / RESULT_FILE
            if not result_path.exists():
                raise SandboxError(f"{self.name}: sandbox did not produce {RESULT_FILE}")
            return db.save_step_result(
                input_table,
                f"SELECT * FROM read_csv_auto({literal(str(result_path))})",
            )

    def _wrapped_code(self, values: dict[str, Any]) -> str:
        """Код инструмента + params: JSON -> Python-литерал, без импортов."""
        return wrap_python_code(self.spec.code or "", values)

    def execute(self, db, input_table: str, args: ToolArgs) -> str:
        raise TypeError(f"{self.name} исполняется только через arun: нужна песочница")
