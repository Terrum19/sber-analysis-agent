/**
 * Типы стрима чата (дубль контракта backend/app/api/schemas/chat.py).
 * Фронт получает события: text | chart | dashboard | error | done.
 */
export interface ChartEvent {
  type: "chart";
  data: { spec: Record<string, unknown>; caption: string };
}

export interface DashboardEvent {
  type: "dashboard";
  data: { dashboard_id: string; title?: string; tiles?: Tile[] };
}

export interface TextEvent {
  type: "text";
  data: { chunk?: string; headline?: string; details?: string[] };
}

export interface Tile {
  title: string;
  spec: Record<string, unknown>;
  sql?: string | null;
  layout?: { x: number; y: number; w: number; h: number };
}

export type ChatStreamEvent = ChartEvent | DashboardEvent | TextEvent | { type: "error" | "done"; data: Record<string, unknown> };
