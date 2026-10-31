/**
 * Типы графиков, поддерживаемые backend-ом
 * (backend/app/core/viz/vega_spec_builder.py: CHART_TYPES).
 *
 * Смена типа на клиенте без LLM: в спеке обновляются mark, usermeta.chart_type
 * и usermeta.requested_type — recalculate.rebuild_spec читает requested_type
 * первым, поэтому оба поля обязательны.
 */
import { isMockMode, rebuildTileSpec } from "@/api/client";
import { MIN_SIZE, isKpiTile } from "@/lib/tileLayout";
import type { DashboardTile } from "@/types/dashboard";
import type { IconName } from "@/components/ui/Icon";

export const CHART_TYPES = [
  "bar",
  "line",
  "area",
  "scatter",
  "histogram",
  "box",
  "heatmap",
  "table",
  "kpi",
] as const;

export type ChartType = (typeof CHART_TYPES)[number];

export const COMPLEX_CHART_TYPES = ["heatmap", "histogram", "box"] as const;

export const CHART_TYPE_ICONS: Record<ChartType, IconName> = {
  bar: "chartBar",
  line: "chartLine",
  area: "chartArea",
  scatter: "chartScatter",
  histogram: "chartHistogram",
  box: "chartBox",
  heatmap: "chartHeatmap",
  table: "chartTable",
  kpi: "chartKpi",
};

export const CHART_MARK: Record<ChartType, unknown> = {
  bar: "bar",
  line: "line",
  area: "area",
  scatter: "point",
  histogram: "bar",
  box: "boxplot",
  heatmap: "rect",
  table: "text",
  kpi: { type: "text", fontSize: 48 },
};

/** Текущий тип плитки: requested_type → chart_type → kpi/bar. */
export function chartTypeOf(tile?: DashboardTile | null): ChartType {
  if (!tile) return "bar";
  const usermeta = tile.spec?.usermeta as Record<string, unknown> | undefined;
  const value = (usermeta?.requested_type ?? usermeta?.chart_type) as string | undefined;
  if (value && (CHART_TYPES as readonly string[]).includes(value)) return value as ChartType;
  return isKpiTile(tile) ? "kpi" : "bar";
}

/**
 * Локальная смена типа графика: mark + usermeta (оба поля) + is_kpi + layout.
 * Данные (spec.data.values) не трогаем — их пересчитает backend.
 * Для heatmap/histogram/box без бэкенда не подменяем mark на чужом encoding:
 * оставляем старый вид до пересборки на бэкенде.
 */
export function withChartType(tile: DashboardTile, type: ChartType): DashboardTile {
  const current = (tile.spec?.usermeta as Record<string, unknown> | undefined) ?? {};
  const currentType = chartTypeOf(tile);
  const isComplex = (COMPLEX_CHART_TYPES as readonly string[]).includes(type);
  const min = type === "kpi" ? MIN_SIZE.kpi : MIN_SIZE.chart;
  const layout = tile.layout ?? { x: 0, y: 0, w: min.w, h: min.h };

  if (isComplex && currentType !== type) {
    return {
      ...tile,
      spec: {
        ...(tile.spec ?? {}),
        usermeta: { ...current, requested_type: type },
      },
      is_kpi: false,
      layout: {
        x: layout.x ?? 0,
        y: layout.y ?? 0,
        w: Math.max(layout.w ?? min.w, min.w),
        h: Math.max(layout.h ?? min.h, min.h),
      },
    };
  }
  const specData = tile.spec?.data as { values?: unknown } | undefined;
  const spec: Record<string, unknown> = {
    ...(tile.spec ?? {}),
    usermeta: { ...current, chart_type: type, requested_type: type },
    mark: CHART_MARK[type],
  };

  // Если в спеке нет data.values, но в snapshot_data есть строки, подставляем их
  // для локального превью графиков и таблиц
  if (
    (!specData || !Array.isArray(specData.values)) &&
    Array.isArray(tile.snapshot_data)
  ) {
    spec.data = { ...(tile.spec?.data ?? {}), values: tile.snapshot_data };
  }

  return {
    ...tile,
    spec,
    is_kpi: type === "kpi",
    layout: {
      x: layout.x ?? 0,
      y: layout.y ?? 0,
      w: Math.max(layout.w ?? min.w, min.w),
      h: Math.max(layout.h ?? min.h, min.h),
    },
  };
}

/**
 * Смена типа с пересборкой спеки на бэкенде по данным плитки. Одна замена
 * mark не годится: у таблицы остаются fold и оси колонок, и «столбцы»
 * рисуются полосами по ячейкам. Бэкенд недоступен — локальная замена.
 */
export async function withRebuiltChartType(
  tile: DashboardTile,
  type: ChartType,
): Promise<DashboardTile> {
  if (isMockMode() || !tile.spec) return withChartType(tile, type);
  try {
    const rebuiltSpec = await rebuildTileSpec(tile.spec, type);
    const min = type === "kpi" ? MIN_SIZE.kpi : MIN_SIZE.chart;
    const layout = tile.layout ?? { x: 0, y: 0, w: min.w, h: min.h };
    return {
      ...tile,
      spec: rebuiltSpec,
      is_kpi: type === "kpi",
      layout: {
        x: layout.x ?? 0,
        y: layout.y ?? 0,
        w: Math.max(layout.w ?? min.w, min.w),
        h: Math.max(layout.h ?? min.h, min.h),
      },
    };
  } catch {
    return withChartType(tile, type);
  }
}
