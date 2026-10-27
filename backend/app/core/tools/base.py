"""Базовый интерфейс инструмента (tool).

Тул = предзаписанный SQL-шаблон (или стат-тест), в который подставляются
типизированные аргументы. Возвращает имя новой temp-таблицы.

Аргументы каждого тула описаны pydantic-моделью ``Args``: из неё же
строится описание для планировщика и валидация перед выполнением.
Позиционный список ``columns`` (старый формат плана) раскладывается по
полям ``Args`` в порядке объявления; именованные аргументы его дополняют.
"""

import json
import threading
import types
import typing
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Annotated, Any, Literal

import duckdb
from pydantic import AfterValidator, BaseModel, ConfigDict, ValidationError, model_validator

if TYPE_CHECKING:
    from app.core.llm.client import LLMClient
    from app.core.memory.session_memory import SessionMemory
    from app.core.sandbox.wasm_runtime import WASMSandbox

ToolKind = Literal["transform", "analysis", "lookup"]


@dataclass(frozen=True)
class ToolContext:
    """Что исполнитель даёт тулу помимо таблицы: LLM, песочница, память сессии.

    None — по умолчанию; ``history`` есть только у шагов хода (read_history).
    """

    llm: "LLMClient | None" = None
    sandbox: "WASMSandbox | None" = None
    history: "SessionMemory | None" = None


_FORBIDDEN_FUNCTIONS = frozenset({"current_setting", "getenv", "getvariable"})
_PARSER_LOCK = threading.Lock()


def column_name(value: str) -> str:
    """Имя таблицы или колонки из плана — как в источнике, без кавычек.

    Имена остаются такими, как их отдал источник (кириллица, пробелы,
    регистр, см. docs/data_connectors.md); в SQL их кавычит identifier().
    """
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"Invalid SQL identifier: {value!r}")
    return value


def identifier(value: str) -> str:
    """Quote a table or column name supplied by a research plan.

    Names stay as the source has them (Cyrillic, spaces, mixed case, see
    docs/data_connectors.md), so the name is double-quoted with embedded
    quotes doubled rather than matched against a pattern.
    """
    return '"' + column_name(value).replace('"', '""') + '"'


@cache
def _parser() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(":memory:")


def _parse_select(sql: str) -> dict:
    with _PARSER_LOCK:
        serialized = _parser().execute("SELECT json_serialize_sql($sql)", {"sql": sql}).fetchone()
    tree = json.loads(serialized[0])
    if tree.get("error") or len(tree.get("statements", [])) != 1:
        raise ValueError("Invalid SQL expression")
    return tree["statements"][0]["node"]


def _forbidden(node: object) -> bool:
    if isinstance(node, list):
        return any(_forbidden(item) for item in node)
    if not isinstance(node, dict):
        return False
    if "SUBQUERY" in (node.get("class"), node.get("type")):
        return True
    if (
        node.get("class") == "FUNCTION"
        and str(node.get("function_name", "")).lower() in _FORBIDDEN_FUNCTIONS
    ):
        return True
    return any(_forbidden(item) for item in node.values())


def expression(value: str) -> str:
    """Validate a scalar SQL expression supplied by a research plan.

    DuckDB parses the expression on its own, in parentheses, exactly as the
    tools embed it. It must be a single scalar expression: no FROM clause,
    no subqueries and no functions that read settings or the environment,
    so it can only use the columns of the table it is applied to.
    """
    if not isinstance(value, str) or not value.strip() or ";" in value or "--" in value:
        raise ValueError("Invalid SQL expression")
    text = value.strip()
    node = _parse_select(f"SELECT ({text})")
    if (
        node.get("type") != "SELECT_NODE"
        or node.get("from_table", {}).get("type") != "EMPTY"
        or len(node.get("select_list", [])) != 1
        or node.get("where_clause")
        or node.get("group_expressions")
        or node.get("having")
        or node.get("qualify")
        or node.get("modifiers")
        or node.get("cte_map", {}).get("map")
        or _forbidden(node["select_list"])
    ):
        raise ValueError("Invalid SQL expression")
    return text


def literal(value: object) -> str:
    """SQL-литерал для значения из плана: числа как есть, строки в кавычках."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def table_name(value: str) -> str:
    """Имя таблицы; ссылки $step_N подставляет исполнитель до валидации."""
    return column_name(value)


Ident = Annotated[str, AfterValidator(column_name)]
Expr = Annotated[str, AfterValidator(expression)]
TableRef = Annotated[str, AfterValidator(table_name)]


class ToolArgs(BaseModel):
    """Базовая модель аргументов тула."""

    model_config = ConfigDict(extra="forbid")


class MetricArgs(ToolArgs):
    """Аргументы с metric_column и без своего column.

    Модель переносит column из group_by/aggregate (#150): принимаем как синоним,
    если metric_column не задан. Описание тула для модели не меняется.
    """

    @model_validator(mode="before")
    @classmethod
    def _column_as_metric(cls, data: Any) -> Any:
        if isinstance(data, dict) and "column" in data and "metric_column" not in data:
            data = dict(data)
            data["metric_column"] = data.pop("column")
        return data


class NoArgs(ToolArgs):
    """Тул без аргументов."""


def _is_list(annotation: object) -> bool:
    """Чисто списочное поле забирает все оставшиеся позиционные аргументы."""
    return typing.get_origin(annotation) is list


def _type_name(annotation: object, metadata: list[object] = ()) -> str:
    validators = [getattr(item, "func", None) for item in metadata]
    if table_name in validators or annotation == TableRef:
        return "table"
    if column_name in validators or annotation == Ident:
        return "column"
    if expression in validators or annotation == Expr:
        return "sql_expression"
    origin = typing.get_origin(annotation)
    if origin is Literal:
        return "|".join(str(arg) for arg in typing.get_args(annotation))
    if origin is list:
        return f"list[{_type_name(typing.get_args(annotation)[0])}]"
    if origin in (typing.Union, types.UnionType):
        return " | ".join(
            _type_name(arg) for arg in typing.get_args(annotation) if arg is not type(None)
        )
    return getattr(annotation, "__name__", str(annotation))


class BaseTool(ABC):
    """Абстрактный инструмент исследования.

    kind:
      - ``transform`` — таблица -> таблица, продолжает основной конвейер;
      - ``analysis`` — сводка/стат-тест, не заменяет основную таблицу;
      - ``lookup`` — разведка данных, после неё план перестраивается.
    """

    name: str = ""
    description: str = ""
    kind: ToolKind = "transform"
    Args: type[ToolArgs] = NoArgs

    def parse_args(
        self, columns: list[Any] | None = None, arguments: dict[str, Any] | None = None
    ) -> ToolArgs:
        """Собрать и провалидировать аргументы (ValueError при ошибке)."""
        data: dict[str, Any] = {}
        positional = list(columns or [])
        for field_name, field in self.Args.model_fields.items():
            if not positional:
                break
            if _is_list(field.annotation):
                data[field_name], positional = positional, []
            else:
                data[field_name] = positional.pop(0)
        if positional:
            raise ValueError(
                f"{self.name}: too many positional arguments; expected {self.signature()}"
            )
        data.update(arguments or {})
        try:
            return self.Args.model_validate(data)
        except ValidationError as exc:
            raise ValueError(
                f"{self.name}: invalid arguments ({exc.errors()[0]['msg']}); "
                f"expected {self.signature()}"
            ) from exc

    def enabled(self) -> bool:
        """Доступен ли тул сейчас; выключенный реестр не выдаёт и не показывает."""
        return True

    def run(
        self, db, input_table: str, columns: list[Any] | None = None, /, **arguments: Any
    ) -> str:
        """Применить тул к input_table, вернуть имя результирующей таблицы."""
        return self.execute(db, input_table, self.parse_args(columns, arguments))

    async def arun(
        self,
        ctx: ToolContext,
        db,
        input_table: str,
        columns: list[Any] | None = None,
        /,
        **arguments: Any,
    ) -> str:
        """Точка входа исполнителя; тулы, которым нужны LLM или песочница, её переопределяют."""
        return self.run(db, input_table, columns, **arguments)

    @abstractmethod
    def execute(self, db, input_table: str, args: Any) -> str:
        """Выполнить тул с уже провалидированными аргументами."""
        raise NotImplementedError

    def signature(self) -> str:
        """Сигнатура аргументов: name: type (обязательный) / name: type = default."""
        parts = []
        for field_name, field in self.Args.model_fields.items():
            text = f"{field_name}: {_type_name(field.annotation, field.metadata)}"
            if not field.is_required():
                default = field.get_default(call_default_factory=True)
                text += f" = {default!r}"
            parts.append(text)
        return "{" + ", ".join(parts) + "}"

    def describe(self) -> str:
        """Описание тула для LLM (в системный промпт планировщика)."""
        return f"{self.name} [{self.kind}]: {self.description}; arguments={self.signature()}"
