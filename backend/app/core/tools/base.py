"""Базовый интерфейс инструмента (tool).

Тул = предзаписанный SQL-шаблон (или стат-тест), в который просто
подставляются имена колонок. Возвращает имя новой temp-таблицы.
"""
from abc import ABC, abstractmethod


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
