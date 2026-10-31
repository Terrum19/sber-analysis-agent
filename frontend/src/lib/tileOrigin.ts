/**
 * Один график в нескольких дашбордах (F-15).
 * Копия плитки получает от бэкенда новый id, поэтому «тот же график» узнаётся
 * по ключу происхождения: origin_tile_id копии или id оригинала.
 */
import type { DashboardTile } from "@/types/dashboard";

export function originKey(tile: DashboardTile): string | undefined {
  return tile.origin_tile_id || tile.id || undefined;
}

/** Нижняя граница видимых плиток: новая плитка встаёт под ними. */
function bottom(tiles: DashboardTile[]): number {
  return tiles
    .filter((tile) => !tile.hidden)
    .reduce((max, tile) => Math.max(max, (tile.layout?.y ?? 0) + (tile.layout?.h ?? 0)), 0);
}

/** Копия плитки без id внизу сетки; сессия и ход — откуда график, если плитка их не знает. */
export function copyTile(tile: DashboardTile, tiles: DashboardTile[], sourceSessionId?: string): DashboardTile {
  return {
    title: tile.title,
    spec: tile.spec,
    result_table: tile.result_table,
    snapshot_data: tile.snapshot_data,
    snapshot_at: tile.snapshot_at,
    insight: tile.insight,
    is_kpi: tile.is_kpi,
    source_step_id: tile.source_step_id,
    origin_tile_id: originKey(tile),
    source_session_id: tile.source_session_id || sourceSessionId,
    source_turn_id: tile.source_turn_id,
    layout: { x: 0, y: bottom(tiles), w: tile.layout?.w ?? 12, h: tile.layout?.h ?? 5 },
  };
}

/**
 * Положить график в плитки дашборда: скрытый оригинал возвращается вниз сетки,
 * иначе добавляется копия.
 */
export function withChart(
  tiles: DashboardTile[],
  tile: DashboardTile,
  sourceSessionId?: string,
): DashboardTile[] {
  const key = originKey(tile);
  const hidden = tiles.findIndex((item) => item.hidden && originKey(item) === key);
  if (hidden === -1) return [...tiles, copyTile(tile, tiles, sourceSessionId)];
  const y = bottom(tiles);
  return tiles.map((item, i) =>
    i === hidden ? { ...item, hidden: false, layout: { ...item.layout, x: 0, y } } : item,
  );
}

/** Убрать график из плиток дашборда: оригинал скрывается, копии удаляются. */
export function withoutChart(tiles: DashboardTile[], tile: DashboardTile): DashboardTile[] {
  const key = originKey(tile);
  return tiles
    .filter((item) => originKey(item) !== key || !item.origin_tile_id)
    .map((item) => (originKey(item) === key ? { ...item, hidden: true } : item));
}

/** Есть ли график среди видимых плиток. */
export function hasChart(tiles: DashboardTile[], tile: DashboardTile): boolean {
  const key = originKey(tile);
  return tiles.some((item) => !item.hidden && originKey(item) === key);
}
