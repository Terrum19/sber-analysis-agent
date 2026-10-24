/**
 * Типы дашборда (синхронизировано с backend/app/api/schemas/dashboard.py).
 * Строго snake_case, как в моделях Pydantic.
 */

export interface DashboardTileLayout {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface DashboardTile {
  id?: string;
  title: string;
  spec: Record<string, unknown>;
  sql?: string | null;
  layout: DashboardTileLayout;
  source_step_id?: number;
  result_table?: string;
  insight?: string;
  is_kpi?: boolean;
  stale?: boolean;
  snapshot_data?: any;
  snapshot_at?: number;
  updated_at?: number | string;
}

export interface DashboardConfig {
  id: string;
  session_id: string;
  title: string;
  template_id?: string | null;
  tiles: DashboardTile[];
  created_at?: number | string;
  updated_at?: number | string;
  /** Дата снимка на уровне дашборда; в B-05 приходит только у плиток. */
  snapshot_at?: number | null;
  is_snapshot?: boolean;
}

export interface DashboardListItem {
  id: string;
  session_id: string;
  title: string;
  created_at?: number | string;
  updated_at?: number | string;
  tiles_count?: number;
}

export interface DashboardCreatePayload {
  session_id: string;
  title?: string;
  tiles?: DashboardTile[];
}

export interface DashboardUpdatePayload {
  title?: string;
  tiles?: DashboardTile[];
}

/** Алиасы контракта бэкенда (B-01 / B-03, Epic #27 / Issue F-07) */
export type DashboardOut = DashboardConfig;
export type DashboardSummary = DashboardListItem;
export type DashboardTileOut = DashboardTile;
export type DashboardCreate = DashboardCreatePayload;
export type DashboardUpdate = DashboardUpdatePayload;
