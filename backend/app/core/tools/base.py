"""Базовый интерфейс инструмента (tool).

Тул = предзаписанный SQL-шаблон (или стат-тест), в который просто
подставляются имена колонок. Возвращает имя новой temp-таблицы.

Тулы-отчёты (transforms_data = False: стат-тесты, поиск значений,
паспорт таблицы) не меняют рабочую таблицу: следующий шаг получает
ту же таблицу, что и они.
"""

import json
import re
import threading
from abc import ABC, abstractmethod
from functools import cache

import duckdb

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FORBIDDEN_FUNCTIONS = frozenset({"current_setting", "getenv", "getvariable"})
_PARSER_LOCK = threading.Lock()


def identifier(value: str) -> str:
    """Validate a SQL identifier supplied by a research plan."""
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"Invalid SQL identifier: {value!r}")
    return value


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


class BaseTool(ABC):
    """Абстрактный инструмент исследования."""

    name: str = ""
    description: str = ""
    sql_template: str = ""  # с плейсхолдерами {table} и {col}
    transforms_data: bool = True

    @abstractmethod
    def run(self, db, input_table: str, columns: list[str], **kwargs: object) -> str:
        """Применить тул к input_table, вернуть имя результирующей таблицы."""
        raise NotImplementedError

    def describe(self) -> str:
        """Описание тула для LLM (в системный промпт планировщика)."""
        return f"{self.name}: {self.description}"
