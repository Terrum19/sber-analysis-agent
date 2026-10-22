"use client";

/**
 * Оформление графиков под текущую тему.
 * Цвета читаются из CSS-переменных --viz-* (globals.css), поэтому графики
 * перекрашиваются вместе с интерфейсом.
 *
 * Цвет несёт смысл, а не украшает:
 * - один ряд — один цвет (--viz-1), название ряда даёт заголовок плитки;
 * - несколько рядов — категориальная палитра в фиксированном порядке
 *   (проверена на различимость при дальтонизме для обеих тем);
 * - отрицательные значения столбцов — цвет --viz-bad.
 */
import { useMemo } from "react";
import type { EChartsOption } from "echarts";
import { useUiStore } from "@/store/uiStore";

export interface ChartColors {
  series: string[];
  good: string;
  bad: string;
  ink: string;
  ink2: string;
  muted: string;
  grid: string;
  axis: string;
  tooltip: string;
}

// Значения тёмной темы на случай рендера до появления document (SSR)
const FALLBACK: ChartColors = {
  series: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
  good: "#0ca30c",
  bad: "#d03b3b",
  ink: "#f0efec",
  ink2: "#c3c2b7",
  muted: "#9c9a92",
  grid: "#34342f",
  axis: "#4a4943",
  tooltip: "#30302e",
};

function readColors(): ChartColors {
  if (typeof document === "undefined") return FALLBACK;
  const css = getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  return {
    series: FALLBACK.series.map((c, i) => v(`--viz-${i + 1}`, c)),
    good: v("--viz-good", FALLBACK.good),
    bad: v("--viz-bad", FALLBACK.bad),
    ink: v("--viz-ink", FALLBACK.ink),
    ink2: v("--viz-ink-2", FALLBACK.ink2),
    muted: v("--viz-muted", FALLBACK.muted),
    grid: v("--viz-grid", FALLBACK.grid),
    axis: v("--viz-axis", FALLBACK.axis),
    tooltip: v("--viz-tooltip", FALLBACK.tooltip),
  };
}

/** Цвета графиков для текущей темы (пересчитываются при её смене). */
export function useChartColors(): ChartColors {
  const theme = useUiStore((s) => s.theme);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => readColors(), [theme]);
}

type VegaSpec = Record<string, any>;

function markType(mark: unknown): string | undefined {
  if (typeof mark === "string") return mark;
  if (mark && typeof mark === "object") return (mark as { type?: string }).type;
  return undefined;
}

/** Убирает «декоративные» цвета из спеки и подставляет тему. */
export function themeVegaSpec(spec: VegaSpec, colors: ChartColors): VegaSpec {
  const next: VegaSpec = { ...spec };
  const type = markType(spec.mark);

  // Цвет марки задаёт тема, а не спека
  if (spec.mark && typeof spec.mark === "object") {
    const { color: _color, fill: _fill, stroke: _stroke, ...mark } = spec.mark as Record<string, unknown>;
    next.mark = mark;
  }

  // Отрицательные столбцы — «плохой» цвет, если ряд один (нет color-кодирования)
  const encoding = (spec.encoding ?? {}) as Record<string, any>;
  if (type === "bar" && !encoding.color) {
    const valueChannel = encoding.y?.type === "quantitative" ? encoding.y : encoding.x?.type === "quantitative" ? encoding.x : null;
    if (valueChannel?.field) {
      next.encoding = {
        ...encoding,
        color: {
          condition: { test: `datum[${JSON.stringify(valueChannel.field)}] < 0`, value: colors.bad },
          value: colors.series[0],
        },
      };
    }
  }

  const font = "system-ui, -apple-system, sans-serif";
  next.background = "transparent";
  next.config = {
    ...(spec.config as object),
    font,
    view: { stroke: "transparent" },
    mark: { color: colors.series[0] },
    bar: { color: colors.series[0], cornerRadiusEnd: 4 },
    line: { color: colors.series[0], strokeWidth: 2 },
    point: { color: colors.series[0], size: 64, filled: true },
    area: { color: colors.series[0], opacity: 0.85 },
    range: { category: colors.series },
    axis: {
      labelColor: colors.muted,
      titleColor: colors.ink2,
      gridColor: colors.grid,
      tickColor: colors.axis,
      domainColor: colors.axis,
      labelFont: font,
      titleFont: font,
      titleFontWeight: 500,
    },
    legend: { labelColor: colors.ink2, titleColor: colors.ink2, labelFont: font, titleFont: font },
    title: { color: colors.ink, font },
    text: { color: colors.ink },
  };
  return next;
}

/** Базовые параметры ECharts под тему (опция графика накладывается поверх). */
export function echartsBase(colors: ChartColors): EChartsOption {
  return {
    backgroundColor: "transparent",
    color: colors.series,
    textStyle: { color: colors.muted, fontFamily: "system-ui, -apple-system, sans-serif" },
    tooltip: {
      trigger: "axis",
      backgroundColor: colors.tooltip,
      borderColor: colors.axis,
      textStyle: { color: colors.ink },
    },
    grid: { top: 24, right: 16, bottom: 24, left: 36, containLabel: true },
  };
}

/** Знак изменения в подписи KPI («+18.4%», «−3 %») для цвета и стрелки. */
export function deltaSign(label: string): 1 | -1 | 0 {
  const m = label.match(/(^|[\s(:])([+\-−])\s?\d/);
  if (!m) return 0;
  return m[2] === "+" ? 1 : -1;
}
