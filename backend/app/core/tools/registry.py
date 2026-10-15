"""Реестр инструментов: имя -> тул.

Центральная точка расширяемости: новый SQL-шаблон/стат-тест/TS-анализ
регистрируется здесь, оркестратор о нём не знает.
"""

from app.core.tools.base import BaseTool


class ToolRegistry:
    """Реестр тулов с диспетчеризацией по имени."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Зарегистрировать тул под его именем."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool:
        """Получить тул по имени (KeyError -> recovery/rephrase)."""
        return self._tools[name]

    def describe(self) -> str:
        """Описания всех тулов для LLM."""
        return "\n".join(t.describe() for t in self._tools.values())


tool_registry = ToolRegistry()
