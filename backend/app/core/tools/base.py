"""Базовый интерфейс инструмента (tool).

Тул = предзаписанный SQL-шаблон (или стат-тест), в который подставляются
типизированные аргументы. Возвращает имя новой temp-таблицы.

Аргументы каждого тула описаны pydantic-моделью ``Args``: из неё же
строится описание для планировщика и валидация перед выполнением.
Позиционный список ``columns`` (старый формат плана) раскладывается по
полям ``Args`` в порядке объявления; именованные аргументы его дополняют.
"""

import re
import types
import typing
from abc import ABC, abstractmethod
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, ValidationError

# Буквы (в т.ч. кириллица), цифры и подчёркивание; не с цифры.
_IDENTIFIER = re.compile(r"^[^\W\d]\w*$")

ToolKind = Literal["transform", "analysis", "lookup"]


def identifier(value: str) -> str:
    """Validate a SQL identifier supplied by a research plan."""
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"Invalid SQL identifier: {value!r}")
    return value


def expression(value: str) -> str:
    """Validate a simple SQL expression supplied by a research plan."""
    if not isinstance(value, str) or not value.strip() or ";" in value or "--" in value:
        raise ValueError("Invalid SQL expression")
    return value.strip()


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
    return identifier(value)


Ident = Annotated[str, AfterValidator(identifier)]
Expr = Annotated[str, AfterValidator(expression)]
TableRef = Annotated[str, AfterValidator(table_name)]


class ToolArgs(BaseModel):
    """Базовая модель аргументов тула."""

    model_config = ConfigDict(extra="forbid")


class NoArgs(ToolArgs):
    """Тул без аргументов."""


def _is_list(annotation: object) -> bool:
    """Чисто списочное поле забирает все оставшиеся позиционные аргументы."""
    return typing.get_origin(annotation) is list


def _type_name(annotation: object, metadata: list[object] = ()) -> str:
    validators = [getattr(item, "func", None) for item in metadata]
    if table_name in validators or annotation == TableRef:
        return "table"
    if identifier in validators or annotation == Ident:
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

    def run(self, db, input_table: str, columns: list[Any] | None = None, **arguments: Any) -> str:
        """Применить тул к input_table, вернуть имя результирующей таблицы."""
        return self.execute(db, input_table, self.parse_args(columns, arguments))

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
