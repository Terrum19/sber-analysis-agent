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
 *
 * Сетка и оси неброские: сетка — только по оси значений, без засечек;
 * числа компактные («20 млн»), даты и разряды — в локали интерфейса.
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
    line: { color: colors.series[0], strokeWidth: 2, strokeCap: "round", strokeJoin: "round" },
    point: { color: colors.series[0], size: 64, filled: true },
    // Заливка площади — лёгкая подложка под линией, а не сплошной блок
    area: { color: colors.series[0], opacity: 0.15, line: { strokeWidth: 2 } },
    range: { category: colors.series },
    numberFormat: ",.2~f",
    axis: {
      labelColor: colors.muted,
      titleColor: colors.ink2,
      gridColor: colors.grid,
      gridWidth: 1,
      tickColor: colors.axis,
      domainColor: colors.axis,
      labelFont: font,
      titleFont: font,
      titleFontWeight: 500,
      labelFontSize: 11,
      titleFontSize: 11,
      labelPadding: 6,
      grid: false,
      ticks: false,
    },
    // Сетка — только по оси значений; саму ось значений заменяет сетка
    axisQuantitative: { grid: true, domain: false, tickCount: 4 },
    legend: {
      labelColor: colors.ink2,
      titleColor: colors.ink2,
      labelFont: font,
      titleFont: font,
      labelFontSize: 11,
      orient: "top",
      symbolSize: 80,
    },
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

/* ========================================================================= */
/*              Подача графика в плитке (принципы визуализации)               */
/* ========================================================================= */

/** Локаль чисел и дат для vega-embed (formatLocale / timeFormatLocale). */
export const VEGA_LOCALE = {
  ru: {
    formatLocale: { decimal: ",", thousands: "\u00a0", grouping: [3], currency: ["", "\u00a0₽"] },
    timeFormatLocale: {
      dateTime: "%A, %e %B %Y г. %X",
      date: "%d.%m.%Y",
      time: "%H:%M:%S",
      periods: ["AM", "PM"],
      days: ["воскресенье", "понедельник", "вторник", "среда", "четверг", "пятница", "суббота"],
      shortDays: ["вс", "пн", "вт", "ср", "чт", "пт", "сб"],
      months: ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"],
      shortMonths: ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"],
    },
  },
  en: {
    formatLocale: { decimal: ".", thousands: ",", grouping: [3], currency: ["$", ""] },
    timeFormatLocale: {
      dateTime: "%x, %X",
      date: "%-m/%-d/%Y",
      time: "%-I:%M:%S %p",
      periods: ["AM", "PM"],
      days: ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"],
      shortDays: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
      months: ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
      shortMonths: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    },
  },
} as const;

type Locale = keyof typeof VEGA_LOCALE;

/** Vega-выражение компактного числа: 20 млн / 4,2 тыс. / 950. */
export function compactExpr(value: string, locale: Locale): string {
  const [b, m, k] = locale === "ru" ? [" млрд", " млн", " тыс."] : ["B", "M", "K"];
  return (
    `abs(${value}) >= 1e9 ? format(${value} / 1e9, '.3~r') + '${b}' : ` +
    `abs(${value}) >= 1e6 ? format(${value} / 1e6, '.3~r') + '${m}' : ` +
    `abs(${value}) >= 1e4 ? format(${value} / 1e3, '.3~r') + '${k}' : ` +
    `format(${value}, ',.2~f')`
  );
}

// Имя колонки без человеческой подписи: sum_revenue, category
const RAW_NAME = /^[A-Za-z0-9_]+$/;

/** Подпись оси/легенды: сырое имя колонки не показываем — ряд называет заголовок плитки. */
function humanTitle(def: any): any {
  if (!def || typeof def !== "object" || !def.field) return def;
  const title = def.title ?? def.field;
  return typeof title === "string" && RAW_NAME.test(title) ? { ...def, title: null } : def;
}

function withoutAggregate(def: any): any {
  if (!def || typeof def !== "object") return def;
  const { aggregate: _aggregate, ...rest } = def;
  return rest;
}

/** Категории с порядком в самих подписях (кварталы, «1–5», годы) не сортируем по значению. */
function looksOrdered(values: unknown[]): boolean {
  return values.length > 0 && values.every((value) => /\d/.test(String(value)));
}

const MAX_BAR = 24;
const MAX_LABELLED_BARS = 12;
const MIN_LABELLED_STEP = 18;
// Высота на категорию у горизонтальных столбцов не больше MAX_STEP px; доля зазора между полосами
const MAX_STEP = 48;
const BAND_PADDING = 0.3;

export interface PresentOptions {
  width: number;
  height: number;
  locale: Locale;
  colors: ChartColors;
  /** Подписи значений у концов столбцов (в мини-превью не нужны). */
  labels?: boolean;
}

/**
 * Спека для плитки: без дубля заголовка, с человеческими подписями,
 * компактными числами и тонкими столбцами. Столбцы по категориям —
 * горизонтально, по убыванию, со значением у конца столбца вместо оси.
 */
export function presentVegaSpec(spec: VegaSpec, options: PresentOptions): VegaSpec {
  const { width, height, locale, colors, labels = true } = options;
  const { title: _title, ...rest } = spec;
  const next: VegaSpec = { ...rest };
  const encoding: Record<string, any> = { ...(spec.encoding ?? {}) };
  for (const channel of ["x", "y", "color", "size", "theta"]) {
    if (encoding[channel]) encoding[channel] = humanTitle(encoding[channel]);
  }
  // Оси значений — компактные числа
  for (const channel of ["x", "y"]) {
    const def = encoding[channel];
    if (def?.type === "quantitative" && def.axis !== null && !def.axis?.labelExpr) {
      encoding[channel] = { ...def, axis: { ...def.axis, labelExpr: compactExpr("datum.value", locale) } };
    }
  }
  next.encoding = encoding;

  if (markType(spec.mark) !== "bar") return next;
  const rows: Record<string, unknown>[] = Array.isArray(spec.data?.values) ? spec.data.values : [];
  const { x, y } = encoding;

  // Столбцы по категориям → горизонтальные: подписи категорий читаются без поворота
  const categorical =
    x?.type === "nominal" && y?.type === "quantitative" && (!encoding.color?.field || encoding.color.field === x.field);
  if (categorical && rows.length > 0) {
    const categories = Array.from(new Set(rows.map((row) => row[x.field])));
    const count = categories.length;
    // Подписи значений — только когда на категорию хватает места по высоте
    const labelled = labels && count <= MAX_LABELLED_BARS && height / count >= MIN_LABELLED_STEP;
    const thickness = Math.max(4, Math.min(MAX_BAR, Math.floor((height / count) * 0.62)));
    const transform: any[] = [...(spec.transform ?? [])];
    if (y.aggregate) {
      transform.push({ aggregate: [{ op: y.aggregate, field: y.field, as: y.field }], groupby: [x.field] });
    }
    if (labelled) {
      transform.push({ calculate: compactExpr(`datum[${JSON.stringify(y.field)}]`, locale), as: "__label" });
    }
    const value = withoutAggregate(y);
    const tooltip = Array.isArray(encoding.tooltip) ? encoding.tooltip.map(withoutAggregate) : encoding.tooltip;
    const { mark: _mark, ...base } = next;
    return {
      ...base,
      // Мало категорий в высокой плитке — не растягиваем их на всю высоту: FittedChart центрирует график
      height: Math.min(height, count * MAX_STEP),
      transform,
      encoding: {
        ...encoding,
        tooltip,
        y: {
          ...x,
          title: null,
          sort: looksOrdered(categories) ? null : "-x",
          scale: { ...x.scale, paddingInner: BAND_PADDING, paddingOuter: BAND_PADDING / 2 },
          // Не влезающие подписи категорий скрываются, а не наезжают друг на друга
          axis: { ...x.axis, labelLimit: Math.round(width * 0.4), domain: false, labelOverlap: "greedy" },
        },
        // Каждое значение подписано у конца столбца — ось значений не нужна
        x: labelled
          ? { ...value, title: null, axis: null, scale: { ...value.scale, nice: false } }
          : { ...value, title: null },
      },
      layer: [
        { mark: { type: "bar", size: thickness, cornerRadiusEnd: 4 } },
        ...(labelled
          ? [
              {
                mark: { type: "text", align: "left", baseline: "middle", dx: 6, fontSize: 11 },
                encoding: { text: { field: "__label" }, color: { value: colors.ink2 } },
              },
            ]
          : []),
      ],
    };
  }

  // Прочие столбцы (по времени, по порядку) — тонкие, не на весь слот
  const band = x?.type === "quantitative" ? y : x;
  if (band?.field && rows.length > 0) {
    const count = new Set(rows.map((row) => row[band.field])).size;
    const length = band === x ? width : height;
    const thickness = Math.max(2, Math.min(MAX_BAR, Math.floor((length / count) * 0.62)));
    const mark = typeof spec.mark === "object" ? spec.mark : { type: "bar" };
    next.mark = { ...mark, size: thickness, cornerRadiusEnd: 4 };
  }
  return next;
}
