/**
 * Раскладка плиток в 12-колоночной сетке. Минимальная высота гарантирует,
 * что заголовок, число/график и инсайт помещаются в ячейку и плитки не наезжают друг на друга.
 * В узком контейнере (stack) плитки идут одной колонкой в исходном порядке.
 */
import type { Layout } from "react-grid-layout";
import type { DashboardTile } from "@/types/dashboard";

export const MIN_ROWS = { kpi: 3, chart: 4 };
/** Уже этой ширины (px) плитки выстраиваются в одну колонку. */
export const STACK_BELOW = 560;

export function tileGridLayout(
  tiles: DashboardTile[],
  isKpi: (tile: DashboardTile) => boolean,
  { stack = false }: { stack?: boolean } = {},
): Layout[] {
  const base = tiles.map((tile, i) => {
    const kpi = isKpi(tile);
    const minH = kpi ? MIN_ROWS.kpi : MIN_ROWS.chart;
    const l = tile.layout;
    return {
      i: tile.id || `tile-${i}`,
      x: typeof l?.x === "number" ? l.x : (i % 2) * 6,
      y: typeof l?.y === "number" ? l.y : Math.floor(i / 2) * 4,
      w: typeof l?.w === "number" && l.w > 0 ? l.w : kpi ? 6 : 12,
      h: Math.max(typeof l?.h === "number" && l.h > 0 ? l.h : kpi ? 3 : 5, minH),
      minW: 3,
      minH,
    };
  });
  if (!stack) return base;

  // Одна колонка: порядок чтения — сверху вниз, слева направо
  let y = 0;
  return [...base]
    .sort((a, b) => a.y - b.y || a.x - b.x)
    .map((item) => {
      const next = { ...item, x: 0, y, w: 12 };
      y += item.h;
      return next;
    });
}
