/**
 * Типы стрима чата (дубль контракта backend/app/api/schemas/chat.py).
 * Фронт получает события: text | chart | dashboard | clarification_needed | error | done.
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

/** Шаг не выполнился: агент ждёт ответа (кнопка-подсказка или текст уточнения). */
export interface ClarificationNeededEvent {
  type: "clarification_needed";
  data: { step_id: number; error: string; suggestions: string[] };
}

export interface Tile {
  title: string;
  spec: Record<string, unknown>;
  sql?: string | null;
  layout?: { x: number; y: number; w: number; h: number };
}

export type ChatStreamEvent =
  | ChartEvent
  | DashboardEvent
  | TextEvent
  | ClarificationNeededEvent
  | { type: "error" | "done"; data: Record<string, unknown> };
