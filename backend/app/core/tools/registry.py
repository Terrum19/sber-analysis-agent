"""Реестр инструментов: имя -> тул.

Центральная точка расширяемости: новый SQL-шаблон/стат-тест/TS-анализ
регистрируется здесь, оркестратор о нём не знает.
"""

from app.core.tools.base import BaseTool
from app.core.tools.domain.avg_order_value import AverageOrderValueTool
from app.core.tools.domain.conversion_funnel import ConversionFunnelTool
from app.core.tools.domain.product_performance import ProductPerformanceTool
from app.core.tools.domain.revenue_analysis import RevenueAnalysisTool
from app.core.tools.domain.seasonal_decompose import SeasonalDecomposeTool
from app.core.tools.domain.trend_analysis import TrendAnalysisTool
from app.core.tools.funnel.cohort_analysis import CohortAnalysisTool
from app.core.tools.funnel.funnel_drop_off import FunnelDropOffTool
from app.core.tools.funnel.funnel_steps import FunnelStepsTool
from app.core.tools.funnel.retention_matrix import RetentionMatrixTool
from app.core.tools.inspect_temp_table import InspectTempTableTool
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
from app.core.tools.stats.bootstrap_corr import BootstrapCorrelationTool
from app.core.tools.stats.chi_square import ChiSquareTool
from app.core.tools.stats.correlation import CorrelationTool
from app.core.tools.stats.describe_stats import DescribeStatsTool
from app.core.tools.stats.ks_test import KSTestTool
from app.core.tools.stats.linear_regression import LinearRegressionTool
from app.core.tools.stats.mann_whitney_u import MannWhitneyUTool
from app.core.tools.stats.pearson_corr import PearsonCorrelationTool
from app.core.tools.stats.search_categorical_values import SearchCategoricalValuesTool
from app.core.tools.stats.shapiro_wilk import ShapiroWilkTool
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

    def register_alias(self, alias: str, tool_name: str) -> None:
        """Register an additional planner-facing name for an existing tool."""
        self._tools[alias] = self.get(tool_name)

    def describe(self) -> str:
        """Описания всех тулов для LLM."""
        unique = {id(tool): tool for tool in self._tools.values()}
        return "\n".join(tool.describe() for tool in unique.values())


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
    BootstrapCorrelationTool(),
    ChiSquareTool(),
    SearchCategoricalValuesTool(),
    InspectTempTableTool(),
    KSTestTool(),
    ShapiroWilkTool(),
    ForecastTool(),
    SeasonalityTool(),
    FunnelStepsTool(),
    FunnelDropOffTool(),
    CohortAnalysisTool(),
    RetentionMatrixTool(),
    RevenueAnalysisTool(),
    ProductPerformanceTool(),
    ConversionFunnelTool(),
    AverageOrderValueTool(),
    TrendAnalysisTool(),
    SeasonalDecomposeTool(),
):
    tool_registry.register(_tool)

# Accept the spelling used in external research plans while keeping the
# Python-friendly canonical name available to callers.
tool_registry.register_alias("funnel-drop-off", "funnel_drop_off")
