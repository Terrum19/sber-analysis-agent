/**
 * Типы дашборда (дубль backend/app/core/dashboards/models.py).
 */
export interface DashboardTile {
  title: string;
  spec: Record<string, unknown>;
  sql?: string | null;
  layout: { x: number; y: number; w: number; h: number };
}

export interface DashboardConfig {
  id: string;
  session_id: string;
  title: string;
  tiles: DashboardTile[];
  created_at?: number;
}
