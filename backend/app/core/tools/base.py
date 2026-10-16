"""Базовый интерфейс инструмента (tool).

Тул = предзаписанный SQL-шаблон (или стат-тест), в который просто
подставляются имена колонок. Возвращает имя новой temp-таблицы.
"""

import re
from abc import ABC, abstractmethod

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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


class BaseTool(ABC):
    """Абстрактный инструмент исследования."""

    name: str = ""
    description: str = ""
    sql_template: str = ""  # с плейсхолдерами {table} и {col}

    @abstractmethod
    def run(self, db, input_table: str, columns: list[str]) -> str:
        """Применить тул к input_table, вернуть имя результирующей таблицы."""
        raise NotImplementedError

    def describe(self) -> str:
        """Описание тула для LLM (в системный промпт планировщика)."""
        return f"{self.name}: {self.description}"
