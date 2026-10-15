"""Реестр инструментов: SQL-шаблоны, стат-тесты, анализ временных рядов."""
from app.core.tools.registry import tool_registry
from app.core.tools.sql.aggregate import AggregateTool
from app.core.tools.sql.time_series import TimeSeriesTool
from app.core.tools.stats.correlation import CorrelationTool
from app.core.tools.timeseries.forecast import ForecastTool
from app.core.tools.timeseries.seasonality import SeasonalityTool

# Регистрируем все доступные тулы
tool_registry.register(AggregateTool())
tool_registry.register(TimeSeriesTool())
tool_registry.register(CorrelationTool())
tool_registry.register(ForecastTool())
tool_registry.register(SeasonalityTool())
