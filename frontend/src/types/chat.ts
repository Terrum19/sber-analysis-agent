/**
 * Типы стрима чата (синхронизировано с backend/app/api/schemas/chat.py).
 * Фронт получает события: text | chart | dashboard | error | done.
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
  data: {
    /** Идентификатор завершённого хода: по нему строится reply_to (F-12). */
    turn_id?: string;
    /** Сколько символов истории обрезано планировщиком. */
    prompt_truncated?: number;
  };
}

/** Шаг упал и не исправился (или план пуст): поток закрывается без done. */
export interface ClarificationEvent {
  type: "clarification_needed";
  data: {
    step_id?: number;
    error: string;
    suggestions: string[];
    /** Ход, к которому относится уточнение (для полноты контракта #166). */
    turn_id?: string;
  };
}

/** Алиас для обратной совместимости. */
export type ClarificationNeededEvent = ClarificationEvent;

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

/** Шаг плана в прогрессе хода (по событиям progress/step). */
export interface StepState {
  step_id: number;
  tool?: string;
  purpose?: string;
  status: "started" | "fixing" | "finished" | "failed";
  attempt?: number;
  error?: string;
  elapsed_ms?: number;
  /** Шаг падал, но исправился. */
  fixed?: boolean;
}

/** Прогресс одного хода, собранный из событий progress. */
export interface TurnProgress {
  planning: boolean;
  total_steps?: number;
  steps: StepState[];
  /** Текущий этап после шагов: insight | chart | dashboard. */
  stage?: ProgressPayload["stage"];
  started_at: number;
  finished_at?: number;
}

/** Применить событие progress к прогрессу хода. */
export function applyProgress(progress: TurnProgress, event: ProgressPayload): TurnProgress {
  const next: TurnProgress = { ...progress, steps: [...progress.steps] };
  if (event.stage === "planning") {
    next.planning = event.status === "started";
    if (event.total_steps !== undefined) next.total_steps = event.total_steps;
  } else if (event.stage === "step" && event.step_id !== undefined) {
    const index = next.steps.findIndex((step) => step.step_id === event.step_id);
    const prev = index >= 0 ? next.steps[index] : undefined;
    const step: StepState = {
      ...prev,
      step_id: event.step_id,
      tool: event.tool ?? prev?.tool,
      purpose: event.purpose ?? prev?.purpose,
      status: event.status,
      attempt: event.attempt ?? prev?.attempt,
      error: event.error ?? prev?.error,
      elapsed_ms: event.elapsed_ms ?? prev?.elapsed_ms,
      fixed: event.status === "finished" && prev?.status === "fixing" ? true : prev?.fixed,
    };
    if (index >= 0) next.steps[index] = step;
    else next.steps.push(step);
    next.planning = false;
  } else {
    next.planning = false;
    next.stage = event.stage;
  }
  return next;
}

/** recovery_response кнопки «Пропустить этот шаг» (см. _apply_recovery). */
export const RECOVERY_SKIP_STEP = JSON.stringify({ action: "skip_step" });
