"""Реестр инструментов: имя -> тул.

Центральная точка расширяемости: новый SQL-шаблон/стат-тест/TS-анализ
регистрируется здесь, оркестратор о нём не знает.
"""

from app.core.tools.base import BaseTool
from app.core.tools.sql.aggregate import AggregateTool
from app.core.tools.sql.atomic import (
    CreateColumnTool,
    FilterRowsTool,
    GroupByTool,
    JoinTablesTool,
    LeftJoinTablesTool,
    PivotTool,
    RenameColumnTool,
    SelectColumnsTool,
    TimeBucketTool,
    WindowTool,
)
from app.core.tools.sql.time_series import TimeSeriesTool
from app.core.tools.stats.anova_one_way import AnovaOneWayTool
from app.core.tools.stats.bootstrap_ci import BootstrapCITool
from app.core.tools.stats.correlation import CorrelationTool
from app.core.tools.stats.describe_stats import DescribeStatsTool
from app.core.tools.stats.linear_regression import LinearRegressionTool
from app.core.tools.stats.mann_whitney_u import MannWhitneyUTool
from app.core.tools.stats.pearson_corr import PearsonCorrelationTool
from app.core.tools.stats.search_categorical_values import SearchCategoricalValuesTool
from app.core.tools.stats.spearman_corr import SpearmanCorrelationTool
from app.core.tools.stats.t_test_dep import PairedTTestTool
from app.core.tools.stats.t_test_ind import IndependentTTestTool
from app.core.tools.timeseries.forecast import ForecastTool
from app.core.tools.timeseries.seasonality import SeasonalityTool


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

for _tool in (
    SelectColumnsTool(),
    FilterRowsTool(),
    CreateColumnTool(),
    RenameColumnTool(),
    JoinTablesTool(),
    LeftJoinTablesTool(),
    GroupByTool(),
    PivotTool(),
    TimeBucketTool(),
    WindowTool("lag", "LAG", "Значение из предыдущей строки"),
    WindowTool("lead", "LEAD", "Значение из следующей строки"),
    AggregateTool(),
    TimeSeriesTool(),
    CorrelationTool(),
    PearsonCorrelationTool(),
    SpearmanCorrelationTool(),
    LinearRegressionTool(),
    AnovaOneWayTool(),
    IndependentTTestTool(),
    PairedTTestTool(),
    MannWhitneyUTool(),
    DescribeStatsTool(),
    BootstrapCITool(),
    SearchCategoricalValuesTool(),
    ForecastTool(),
    SeasonalityTool(),
):
    tool_registry.register(_tool)
