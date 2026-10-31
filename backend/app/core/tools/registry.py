"""Реестр инструментов: имя -> тул.

Центральная точка расширяемости: новый SQL-шаблон/стат-тест/TS-анализ
регистрируется здесь, оркестратор о нём не знает.
"""

import threading
from collections.abc import Iterable

from app.config import settings
from app.core.llm.prompts.quality_rules import TOOL_HINTS
from app.core.tools.base import BaseTool
from app.core.tools.codegen import CodegenTool
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
from app.core.tools.read_history import ReadHistoryTool
from app.core.tools.sql.aggregate import AggregateTool
from app.core.tools.sql.atomic import (
    CreateColumnTool,
    FilterRowsTool,
    GroupByTool,
    JoinTablesTool,
    LeftJoinTablesTool,
    PivotTool,
    RenameColumnTool,
    SampleTool,
    SelectColumnsTool,
    SortLimitTool,
    TimeBucketTool,
    WindowMetricTool,
    WindowTool,
)
from app.core.tools.sql.join_related import JoinRelatedTool
from app.core.tools.sql.time_series import TimeSeriesTool
from app.core.tools.stats.anova_one_way import AnovaOneWayTool
from app.core.tools.stats.bootstrap_ci import BootstrapCITool
from app.core.tools.stats.bootstrap_corr import BootstrapCorrelationTool
from app.core.tools.stats.chi_square import ChiSquareTool
from app.core.tools.stats.correlation import CorrelationTool
from app.core.tools.stats.describe_stats import DescribeStatsTool
from app.core.tools.stats.detect_anomalies import DetectAnomaliesTool
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
from app.core.tools.timeseries.lag_correlation import LagCorrelationTool
from app.core.tools.timeseries.period_compare import PeriodCompareTool
from app.core.tools.timeseries.seasonality import SeasonalityTool
from app.models.generated_tool import GeneratedToolSpec


class ToolRegistry:
    """Реестр тулов с диспетчеризацией по имени."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._hidden: set[str] = set()
        # Спеки инструментов, созданных toolgen: только они сохраняются на диск.
        self._generated: dict[str, GeneratedToolSpec] = {}
        self._lock = threading.RLock()

    def register(self, tool: BaseTool, *, visible: bool = True) -> None:
        """Зарегистрировать тул под его именем.

        visible=False — тул исполним, но не показывается планировщику
        (дубли и совместимость со старыми планами).
        """
        with self._lock:
            self._tools[tool.name] = tool
            if visible:
                self._hidden.discard(tool.name)
            else:
                self._hidden.add(tool.name)

    def get(self, name: str) -> BaseTool:
        """Получить тул по имени (KeyError -> recovery/rephrase).

        Выключенный тул (enabled() = False) — как незарегистрированный.
        """
        tool = self._tools[name]
        if not tool.enabled():
            raise KeyError(name)
        return tool

    def register_alias(self, alias: str, tool_name: str) -> None:
        """Register an additional planner-facing name for an existing tool."""
        self._tools[alias] = self.get(tool_name)
        self._hidden.add(alias)

    def register_generated(self, tool: BaseTool, spec: GeneratedToolSpec) -> None:
        """Зарегистрировать проверенный toolgen-инструмент.

        Только этот путь попадает в ``_generated`` (и сохраняется на диск).
        Коллизия имени или превышение лимита — ValueError.
        """
        if not spec.feasible:
            raise ValueError(f"tool {spec.name!r} is not feasible")
        if tool.name != spec.name:
            raise ValueError(f"tool name {tool.name!r} does not match spec name {spec.name!r}")
        with self._lock:
            if spec.name in self._tools:
                raise ValueError(f"tool {spec.name!r} is already registered")
            if len(self._generated) >= settings.toolgen_max_tools:
                raise ValueError(f"generated tools limit reached ({settings.toolgen_max_tools})")
            self._tools[spec.name] = tool
            self._generated[spec.name] = spec
            self._hidden.discard(spec.name)

    def unregister_generated(self, names: Iterable[str] | None = None) -> list[str]:
        """Убрать generated-инструменты (все или указанные); вернуть имена."""
        with self._lock:
            targets = list(self._generated) if names is None else list(names)
            removed = []
            for name in targets:
                if name in self._generated:
                    self._generated.pop(name, None)
                    self._tools.pop(name, None)
                    self._hidden.discard(name)
                    removed.append(name)
            return removed

    def generated_tools(self) -> list[BaseTool]:
        """Инструменты, созданные toolgen (в порядке регистрации)."""
        with self._lock:
            return [self._tools[name] for name in self._generated if name in self._tools]

    def generated_specs(self) -> dict[str, GeneratedToolSpec]:
        """Спеки generated-инструментов (копия)."""
        with self._lock:
            return dict(self._generated)

    def names(self) -> set[str]:
        """Все имена реестра, включая скрытые и алиасы (для проверки коллизий)."""
        with self._lock:
            return set(self._tools)

    def load_generated(self, store=None) -> list[str]:
        """Загрузить проверенные спеки при старте (см. toolgen.factory)."""
        from app.core.tools.toolgen.factory import load_generated_tools

        return load_generated_tools(self, store)

    def planner_tools(self) -> list[BaseTool]:
        """Тулы, которые видит планировщик."""
        return [
            tool
            for name, tool in self._tools.items()
            if name not in self._hidden and tool.enabled()
        ]

    def describe(self) -> str:
        """Описания тулов для LLM (по одному на строку)."""
        hints = TOOL_HINTS if settings.agent_quality_fixes else {}
        return "\n".join(
            f"- {tool.describe()}{hints.get(tool.name, '')}" for tool in self.planner_tools()
        )

    def tool_schema(self, name: str):
        """Typed JSON schema тула по имени (P1)."""
        from app.core.tools.schema import tool_schema

        return tool_schema(self.get(name))

    def describe_for_schema(self, names: list[str] | None = None) -> dict[str, dict]:
        """Схемы аргументов выбранных тулов (для typed planner_schema)."""
        chosen = names or [tool.name for tool in self.planner_tools()]
        return {name: self.tool_schema(name).json_schema for name in chosen}


tool_registry = ToolRegistry()

for _tool in (
    SelectColumnsTool(),
    FilterRowsTool(),
    CreateColumnTool(),
    RenameColumnTool(),
    JoinTablesTool(),
    LeftJoinTablesTool(),
    JoinRelatedTool(),
    GroupByTool(),
    PivotTool(),
    TimeBucketTool(),
    SortLimitTool(),
    SampleTool(),
    WindowTool("lag", "LAG", "Значение из предыдущей строки (offset строк назад)"),
    WindowTool("lead", "LEAD", "Значение из следующей строки (offset строк вперёд)"),
    WindowMetricTool(),
    AggregateTool(),
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
    ReadHistoryTool(),
    KSTestTool(),
    ShapiroWilkTool(),
    ForecastTool(),
    SeasonalityTool(),
    PeriodCompareTool(),
    LagCorrelationTool(),
    DetectAnomaliesTool(),
    FunnelStepsTool(),
    FunnelDropOffTool(),
    CohortAnalysisTool(),
    RetentionMatrixTool(),
    RevenueAnalysisTool(),
    AverageOrderValueTool(),
    TrendAnalysisTool(),
    SeasonalDecomposeTool(),
    CodegenTool(),  # виден, только если CODEGEN_ENABLED и песочница установлена
):
    tool_registry.register(_tool)

# Дубли: исполняются для старых планов, но планировщику не показываются.
for _tool in (
    TimeSeriesTool(),  # = time_bucket(metric_column, function)
    CorrelationTool(),  # = pearson_corr без p-value
    ProductPerformanceTool(),  # = group_by с metrics
    ConversionFunnelTool(),  # = funnel_steps
):
    tool_registry.register(_tool, visible=False)

# Accept the spelling used in external research plans while keeping the
# Python-friendly canonical name available to callers.
tool_registry.register_alias("funnel-drop-off", "funnel_drop_off")
