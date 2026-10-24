/**
 * Типы графиков, поддерживаемые backend-ом
 * (backend/app/core/viz/vega_spec_builder.py: CHART_TYPES).
 *
 * Смена типа на клиенте без LLM: в спеке обновляются mark, usermeta.chart_type
 * и usermeta.requested_type — recalculate.rebuild_spec читает requested_type
 * первым, поэтому оба поля обязательны.
 */
import { MIN_SIZE, isKpiTile } from "@/lib/tileLayout";
import type { DashboardTile } from "@/types/dashboard";

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
 */
export function withChartType(tile: DashboardTile, type: ChartType): DashboardTile {
  const current = (tile.spec?.usermeta as Record<string, unknown> | undefined) ?? {};
  const min = type === "kpi" ? MIN_SIZE.kpi : MIN_SIZE.chart;
  const layout = tile.layout ?? { x: 0, y: 0, w: min.w, h: min.h };
  return {
    ...tile,
    spec: {
      ...(tile.spec ?? {}),
      usermeta: { ...current, chart_type: type, requested_type: type },
      mark: CHART_MARK[type],
    },
    is_kpi: type === "kpi",
    layout: {
      x: layout.x ?? 0,
      y: layout.y ?? 0,
      w: Math.max(layout.w ?? min.w, min.w),
      h: Math.max(layout.h ?? min.h, min.h),
    },
  };
}
