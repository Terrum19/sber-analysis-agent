/**
 * Типы стрима чата (синхронизировано с backend/app/api/schemas/chat.py).
 * Фронт получает события: text | chart | dashboard | error | done |
 * clarification_needed | progress | refusal (refusal — A-05, бэкенд ещё не шлёт).
 */
import type { DashboardTile } from "./dashboard";

export type Tile = DashboardTile;

export interface ChartEvent {
  type: "chart";
  data: {
    spec: Record<string, unknown>;
    caption: string;
    result_table?: string;
  };
}

export interface DashboardEvent {
  type: "dashboard";
  data: {
    dashboard_id: string;
    title?: string;
    tiles?: DashboardTile[];
  };
}

export interface TextEvent {
  type: "text";
  data: {
    chunk?: string;
    headline?: string;
    details?: string[];
  };
}

export interface ErrorEvent {
  type: "error";
  data: {
    message?: string;
    detail?: string | Record<string, unknown>;
    step?: number;
  };
}

export interface DoneEvent {
  type: "done";
  data: Record<string, unknown>;
}

/** Шаг упал и не исправился (или план пуст): поток закрывается без done. */
export interface ClarificationEvent {
  type: "clarification_needed";
  data: {
    step_id: number;
    error: string;
    suggestions: string[];
  };
}

/** Вопрос вне возможностей агента (A-05): причина и переформулировки. */
export interface RefusalEvent {
  type: "refusal";
  data: {
    reason: string;
    suggestions: string[];
  };
}

/** ProgressPayload бэкенда; пустые поля не приходят. */
export interface ProgressPayload {
  stage: "planning" | "step" | "insight" | "chart" | "dashboard";
  status: "started" | "finished" | "failed" | "fixing";
  step_id?: number;
  tool?: string;
  purpose?: string;
  total_steps?: number;
  elapsed_ms?: number;
  attempt?: number;
  error?: string;
}

export interface ProgressEvent {
  type: "progress";
  data: ProgressPayload;
}

export type ChatStreamEvent =
  | ChartEvent
  | DashboardEvent
  | TextEvent
  | ErrorEvent
  | DoneEvent
  | ClarificationEvent
  | RefusalEvent
  | ProgressEvent;

/** recovery_response кнопки «Пропустить этот шаг» (см. _apply_recovery). */
export const RECOVERY_SKIP_STEP = JSON.stringify({ action: "skip_step" });
