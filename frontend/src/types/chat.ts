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
export interface PlannedStepSummary {
  step_id: number;
  purpose: string;
  tool?: string;
}

export interface ProgressPayload {
  stage: "planning" | "replanning" | "step" | "insight" | "chart" | "dashboard";
  status: "started" | "finished" | "failed" | "fixing";
  step_id?: number;
  tool?: string;
  purpose?: string;
  total_steps?: number;
  elapsed_ms?: number;
  attempt?: number;
  error?: string;
  planned_steps?: PlannedStepSummary[];
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

/** Стадия хода в хронологии выполнения (планирование, инсайт, график и т.д.). */
export interface TimelineStage {
  kind: "stage";
  id: string;
  stage: "planning" | "replanning" | "insight" | "chart" | "dashboard";
  labelKey: string;
  started_at: number;
  finished_at?: number;
  elapsed_ms?: number;
  planned_steps?: PlannedStepSummary[];
}

/** Шаг плана в хронологии выполнения. */
export interface TimelineStep {
  kind: "step";
  id: string;
  step: StepState;
}

export type TimelineEntry = TimelineStage | TimelineStep;

/** Прогресс одного хода, собранный из событий progress. */
export interface TurnProgress {
  planning: boolean;
  total_steps?: number;
  steps: StepState[];
  /** Полная хронология всех этапов и шагов для детального отчёта */
  timeline?: TimelineEntry[];
  /** Текущий этап после шагов: replanning | insight | chart | dashboard. */
  stage?: ProgressPayload["stage"];
  started_at: number;
  finished_at?: number;
  /** Шаги, запланированные моделью (если есть) */
  planned_steps?: PlannedStepSummary[];
}

/** Применить событие progress к прогрессу хода. */
export function applyProgress(progress: TurnProgress, event: ProgressPayload): TurnProgress {
  const now = Date.now();
  const next: TurnProgress = {
    ...progress,
    steps: [...progress.steps],
    timeline: progress.timeline ? [...progress.timeline] : [],
  };

  const closeOpenStage = (targetStage?: string) => {
    for (let i = next.timeline!.length - 1; i >= 0; i--) {
      const entry = next.timeline![i];
      if (entry.kind === "stage" && entry.finished_at === undefined) {
        if (!targetStage || entry.stage === targetStage) {
          entry.finished_at = now;
          entry.elapsed_ms = event.elapsed_ms ?? Math.max(1, now - entry.started_at);
          if (event.planned_steps) {
            entry.planned_steps = event.planned_steps;
          }
        }
      }
    }
  };

  if (event.stage === "planning") {
    if (event.status === "started") {
      next.planning = true;
      next.stage = undefined;
      next.timeline!.push({
        kind: "stage",
        id: `planning-${now}`,
        stage: "planning",
        labelKey: "progress.stage.planning",
        started_at: now,
      });
    } else {
      next.planning = false;
      if (event.total_steps !== undefined) next.total_steps = event.total_steps;
      if (event.planned_steps) next.planned_steps = event.planned_steps;
      closeOpenStage("planning");
    }
  } else if (event.stage === "replanning") {
    if (event.status === "started") {
      next.planning = false;
      next.stage = "replanning";
      next.timeline!.push({
        kind: "stage",
        id: `replanning-${now}`,
        stage: "replanning",
        labelKey: "progress.stage.replanning",
        started_at: now,
      });
    } else {
      next.stage = undefined;
      if (event.total_steps !== undefined) next.total_steps = event.total_steps;
      if (event.planned_steps) next.planned_steps = event.planned_steps;
      closeOpenStage("replanning");
    }
  } else if (event.stage === "step" && event.step_id !== undefined) {
    closeOpenStage();
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

    const tIndex = next.timeline!.findIndex(
      (e) => e.kind === "step" && e.step.step_id === event.step_id
    );
    if (tIndex >= 0) {
      next.timeline![tIndex] = { kind: "step", id: `step-${event.step_id}`, step };
    } else {
      next.timeline!.push({ kind: "step", id: `step-${event.step_id}`, step });
    }
    next.planning = false;
  } else if (event.stage !== "step") {
    // insight, chart, dashboard
    if (event.status === "started") {
      closeOpenStage();
      next.planning = false;
      next.stage = event.stage;
      next.timeline!.push({
        kind: "stage",
        id: `${event.stage}-${now}`,
        stage: event.stage,
        labelKey: `progress.stage.${event.stage}`,
        started_at: now,
      });
    } else if (event.status === "finished" || event.status === "failed") {
      closeOpenStage(event.stage);
      next.stage = undefined;
    }
  }
  return next;
}

/** recovery_response кнопки «Пропустить этот шаг» (см. _apply_recovery). */
export const RECOVERY_SKIP_STEP = JSON.stringify({ action: "skip_step" });
