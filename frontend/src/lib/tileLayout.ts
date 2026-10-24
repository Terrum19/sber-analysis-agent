/**
 * Раскладка плиток в 12-колоночной сетке. Минимальный размер гарантирует,
 * что заголовок, число/график и инсайт помещаются в ячейку и плитки не наезжают
 * друг на друга: KPI — 2×2, график — 4×3 (rowHeight = 60 px).
 * В узком контейнере (stack) плитки идут одной колонкой в исходном порядке.
 */
import type { Layout } from "react-grid-layout";
import type { DashboardTile } from "@/types/dashboard";

export const COLS = 12;

export const MIN_SIZE = {
  kpi: { w: 2, h: 2 },
  chart: { w: 4, h: 3 },
} as const;

/** Уже этой ширины (px) плитки выстраиваются в одну колонку. */
export const STACK_BELOW = 560;

/** KPI определяется флагом или usermeta.chart_type/requested_type (mark "text"
 * бывает и у таблицы, поэтому одного mark недостаточно). */
export function isKpiTile(tile: DashboardTile): boolean {
  if (tile.is_kpi) return true;
  const usermeta = tile.spec?.usermeta as Record<string, unknown> | undefined;
  const chartType = (usermeta?.requested_type ?? usermeta?.chart_type) as string | undefined;
  if (chartType) return chartType === "kpi";
  return tile.spec?.mark === "text";
}

/** Раскладка одной плитки: значения из API, fallback и clamp по cols=12. */
export function layoutFromTile(
  tile: DashboardTile,
  index: number,
  kpi: boolean = isKpiTile(tile),
): Layout {
  const min = kpi ? MIN_SIZE.kpi : MIN_SIZE.chart;
  const l = tile.layout;
  const rawW = typeof l?.w === "number" && l.w > 0 ? l.w : kpi ? 6 : COLS;
  const rawH = typeof l?.h === "number" && l.h > 0 ? l.h : kpi ? 3 : 5;
  const rawX = typeof l?.x === "number" ? l.x : (index % 2) * 6;
  const x = Math.max(0, Math.min(rawX, COLS - min.w));
  const w = Math.max(min.w, Math.min(rawW, COLS - x));
  const h = Math.max(rawH, min.h);
  const y = typeof l?.y === "number" ? l.y : Math.floor(index / 2) * 4;
  return { i: tile.id || `tile-${index}`, x, y, w, h, minW: min.w, minH: min.h };
}

/** Применить раскладку react-grid-layout к плиткам (id плитки = ключ layout). */
export function applyGridLayout(tiles: DashboardTile[], layout: Layout[]): DashboardTile[] {
  const byKey = new Map(layout.map((item) => [item.i, item]));
  return tiles.map((tile, i) => {
    const item = byKey.get(tile.id || `tile-${i}`);
    return item
      ? { ...tile, layout: { x: item.x, y: item.y, w: item.w, h: item.h } }
      : tile;
  });
}

export function tileGridLayout(
  tiles: DashboardTile[],
  isKpi: (tile: DashboardTile) => boolean,
  { stack = false }: { stack?: boolean } = {},
): Layout[] {
  const base = tiles.map((tile, i) => layoutFromTile(tile, i, isKpi(tile)));
  if (!stack) return base;

  // Одна колонка: порядок чтения — сверху вниз, слева направо
  let y = 0;
  return [...base]
    .sort((a, b) => a.y - b.y || a.x - b.x)
    .map((item) => {
      const next = { ...item, x: 0, y, w: COLS };
      y += item.h;
      return next;
    });
}

/** Компактное превью в ленте чата: две колонки, KPI может стоять в паре. */
export const PREVIEW_GAP = 8;

export interface PreviewItem {
  key: string;
  tile: DashboardTile;
  /** 1 — KPI в половину строки, 2 — график во всю ширину. */
  span: 1 | 2;
}

/** Порядок чтения как на полной странице: сначала layout.y, затем layout.x. */
export function previewGrid(
  tiles: DashboardTile[],
  isKpi: (tile: DashboardTile) => boolean,
): PreviewItem[] {
  return [...tiles]
    .sort(
      (a, b) =>
        (a.layout?.y ?? 0) - (b.layout?.y ?? 0) || (a.layout?.x ?? 0) - (b.layout?.x ?? 0),
    )
    .map((tile, i) => ({
      key: tile.id || `tile-${i}`,
      tile,
      span: isKpi(tile) ? 1 : 2,
    }));
}
