"""Декларативная спецификация инструмента, созданного агентом (toolgen).

Инструмент описывается данными, а не Python-модулем: SQL-шаблон или
фиксированный Python-код для WASM-песочницы. Такую спеку можно проверить
до регистрации и безопасно хранить между ходами (docs/architecture.md).

``feasible=false`` — модель честно сказала, что инструмент по этому запросу
не нужен или невозможен; остальные поля тогда могут быть пустыми.
"""

import keyword
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Имена ограничены: планировщик должен узнавать инструмент в JSON-плане без
# кавычек, а реестр — не пересекаться с built-in тулами.
TOOL_NAME_PATTERN = r"^[a-z][a-z0-9_]{2,40}$"
PARAMETER_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,40}$"

TOOL_NAME = re.compile(TOOL_NAME_PATTERN)
PARAMETER_NAME = re.compile(PARAMETER_NAME_PATTERN)

# Зарезервированные имена: input — таблица шага, остальные — служебные
# переменные Python-адаптера.
RESERVED_PARAMETER_NAMES = frozenset(
    {"input", "df", "params", "result", "true", "false", "none", "null"}
)

ParameterType = Literal["column", "columns", "string", "integer", "number", "boolean", "enum"]


class ToolParameterSpec(BaseModel):
    """Один типизированный параметр инструмента."""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: ParameterType
    description: str = Field(default="", max_length=200)
    required: bool = True
    default: str | int | float | bool | None = None
    # Только для type=enum: допустимые значения.
    values: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        value = value.strip()
        if not PARAMETER_NAME.fullmatch(value):
            raise ValueError(
                "parameter name must be snake_case: a-z, digits and underscores, "
                "start with a letter (1-41 chars)"
            )
        if keyword.iskeyword(value) or value in RESERVED_PARAMETER_NAMES:
            raise ValueError(f"parameter name {value!r} is reserved")
        return value

    @field_validator("values")
    @classmethod
    def _unique_values(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value.strip()]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("enum values must be unique")
        return cleaned

    @model_validator(mode="after")
    def _enum_shape(self) -> "ToolParameterSpec":
        if self.type == "enum":
            if not self.values:
                raise ValueError("enum parameter requires values")
            if self.default is not None and str(self.default) not in self.values:
                raise ValueError("enum default must be one of values")
        elif self.values:
            raise ValueError("values are only allowed for enum parameters")
        return self


class ToolSampleArgument(BaseModel):
    """Пример значения параметра для smoke-проверки инструмента."""

    model_config = ConfigDict(extra="forbid")

    name: str
    value: Any = None


class GeneratedToolSpec(BaseModel):
    """Проверяемая спецификация инструмента, которую проектирует LLM."""

    model_config = ConfigDict(extra="forbid")

    spec_version: int = 1
    feasible: bool = True
    reason: str = Field(default="", max_length=1000)
    name: str = ""
    description: str = Field(default="", max_length=300)
    kind: Literal["transform", "analysis"] = "transform"
    language: Literal["sql", "python"] = "sql"
    parameters: list[ToolParameterSpec] = Field(default_factory=list, max_length=10)
    sql: str | None = Field(default=None, max_length=8000)
    code: str | None = Field(default=None, max_length=12000)
    sample_arguments: list[ToolSampleArgument] = Field(default_factory=list, max_length=20)
    # Независимый запрос с тем же результатом; не обязателен для простых проекций.
    reference_sql: str | None = Field(default=None, max_length=8000)
    # SQL-утверждения над результатом (__tool_result).
    checks: list[str] = Field(default_factory=list, max_length=5)
    allow_empty: bool = False

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return value
        if not TOOL_NAME.fullmatch(value):
            raise ValueError(
                "tool name must be snake_case: a-z, digits and underscores, "
                "start with a letter (3-41 chars)"
            )
        if keyword.iskeyword(value):
            raise ValueError(f"tool name {value!r} is a Python keyword")
        return value

    @field_validator("description")
    @classmethod
    def _one_line_description(cls, value: str) -> str:
        value = " ".join(value.split())
        if ";" in value:
            raise ValueError("description must not contain ';'")
        return value

    @field_validator("parameters", mode="before")
    @classmethod
    def _parameters_from_dict(cls, value: Any) -> Any:
        """LLM иногда присылает {"column": {"type": "column"}} вместо списка."""
        if isinstance(value, dict):
            return [
                {"name": name, **(spec if isinstance(spec, dict) else {"type": spec})}
                for name, spec in value.items()
            ]
        return value

    @field_validator("checks", mode="before")
    @classmethod
    def _one_check_is_list(cls, value: Any) -> Any:
        return [value] if isinstance(value, str) else value

    @field_validator("checks")
    @classmethod
    def _check_shape(cls, checks: list[str]) -> list[str]:
        cleaned = [check.strip() for check in checks if check.strip()]
        for check in cleaned:
            if len(check) > 2000:
                raise ValueError("check is too long (max 2000 chars)")
        return cleaned

    @field_validator("sample_arguments", mode="before")
    @classmethod
    def _arguments_from_dict(cls, value: Any) -> Any:
        """LLM иногда присылает {"column": "region"} вместо списка пар."""
        if isinstance(value, dict):
            return [{"name": name, "value": item} for name, item in value.items()]
        return value

    @model_validator(mode="after")
    def _shape(self) -> "GeneratedToolSpec":
        if not self.feasible:
            return self
        if not self.name:
            raise ValueError("name is required for a feasible tool")
        if not self.description:
            raise ValueError("description is required for a feasible tool")
        names = [parameter.name for parameter in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("parameter names must be unique")
        if self.language == "sql":
            if not self.sql or not self.sql.strip():
                raise ValueError("SQL tool requires sql")
            if self.code is not None:
                raise ValueError("SQL tool must not contain code")
        else:
            if not self.code or not self.code.strip():
                raise ValueError("Python tool requires code")
            if self.sql is not None:
                raise ValueError("Python tool must not contain sql")
        sample_names = [argument.name for argument in self.sample_arguments]
        if len(sample_names) != len(set(sample_names)):
            raise ValueError("sample argument names must be unique")
        unknown = set(sample_names) - set(names)
        if unknown:
            raise ValueError(f"sample arguments for unknown parameters: {sorted(unknown)}")
        return self
