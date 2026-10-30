/**
 * Типы стрима чата (синхронизировано с backend/app/api/schemas/chat.py).
 * Фронт получает события: text | chart | dashboard | error | done,
 * в режиме дашборда (#224) ещё dashboard_outline и dashboard_fallback,
 * table — агент сам взял таблицу под вопрос.
 */
import type { MessageKey } from "@/i18n/dictionary";
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

/** Вариант дашборда из POST /sessions/{id}/dashboard-ideas. */
export interface DashboardIdea {
  id: string;
  title: string;
  description: string;
}

export type TileIntent = "kpi" | "compare" | "trend" | "distribution" | "relation" | "share";

/** План дашборда: плитки, которые начнут строиться. */
export interface DashboardOutlineEvent {
  type: "dashboard_outline";
  data: {
    title: string;
    tiles: { tile_id: number; title: string; intent: TileIntent }[];
  };
}

/** Запроса не хватило: дашборд строится по первой идее, остальные — кнопками. */
export interface DashboardFallbackEvent {
  type: "dashboard_fallback";
  data: {
    message: string;
    idea: DashboardIdea;
    alternatives: DashboardIdea[];
  };
}

/** Агент сам взял таблицу под вопрос (без таблицы сессии или переход к другим данным). */
export interface TableEvent {
  type: "table";
  data: {
    table: string;
    previous: string | null;
    title: string | null;
  };
}

export interface TextEvent {
  type: "text";
  data: {
    chunk?: string;
    headline?: string;
    details?: string[];
    limits?: string;
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
    /** "table" — выбор данных (#165): suggestions — названия таблиц, ответ одним нажатием. */
    kind?: "table";
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
  /** Назначение шага; бэкенд отдаёт purpose или tool, без выдуманного «Шаг N». */
  purpose?: string;
  tool?: string;
}

/** Реальная фаза планирования, которую сообщает StepPlanner.plan. */
export type PlanningSubstage = "semantic" | "llm" | "validation";

export interface ProgressPayload {
  stage:
    | "planning"
    | "replanning"
    | "step"
    | "insight"
    | "chart"
    | "dashboard"
    | "outline"
    | "tile"
    | "toolgen";
  status: "started" | "substage" | "finished" | "failed" | "fixing";
  step_id?: number;
  tool?: string;
  purpose?: string;
  total_steps?: number;
  elapsed_ms?: number;
  attempt?: number;
  error?: string;
  /** Фаза планирования при status="substage". */
  substage?: PlanningSubstage;
  planned_steps?: PlannedStepSummary[];
  /** Плитка дашборда, к которой относится шаг или статус. */
  tile_id?: number;
}

export interface ProgressEvent {
  type: "progress";
  data: ProgressPayload;
}

export type ChatStreamEvent =
  | ChartEvent
  | DashboardEvent
  | DashboardOutlineEvent
  | DashboardFallbackEvent
  | TableEvent
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
  tile_id?: number;
}

/** Плитка дашборда в прогрессе хода. */
export interface TileState {
  tile_id: number;
  title: string;
  intent?: TileIntent;
  status: "queued" | "started" | "finished" | "failed";
  error?: string;
  elapsed_ms?: number;
}

/** Стадия хода в хронологии выполнения (планирование, инсайт, график и т.д.). */
export interface TimelineStage {
  kind: "stage";
  id: string;
  stage: "planning" | "replanning" | "insight" | "chart" | "dashboard";
  labelKey: MessageKey;
  started_at: number;
  finished_at?: number;
  elapsed_ms?: number;
  /** Чем кончилась стадия: иначе упавший этап выглядел бы успешным. */
  status: "started" | "finished" | "failed";
  /** Текст ошибки упавшей стадии (tooltip в StepProgress). */
  error?: string;
  /** Последняя реальная фаза планирования (semantic -> llm -> validation). */
  substage?: PlanningSubstage;
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
  /** Последнее событие хода: по нему видно, что агент затих. */
  last_event_at?: number;
  /** Режим дашборда: идёт план плиток (stage outline). */
  outlining?: boolean;
  /** Плитки из dashboard_outline; есть только в режиме дашборда. */
  tiles?: TileState[];
  /** Агент создаёт и проверяет новый инструмент (toolgen). */
  toolgen?: boolean;
}

/** Новая стадия в хронологии: status меняется при закрытии. */
function openStage(
  stage: TimelineStage["stage"],
  now: number,
  labelKey: MessageKey
): TimelineStage {
  return {
    kind: "stage",
    id: `${stage}-${now}`,
    stage,
    labelKey,
    started_at: now,
    status: "started",
  };
}

/** Применить событие progress к прогрессу хода, не меняя старое состояние. */
export function applyProgress(progress: TurnProgress, event: ProgressPayload): TurnProgress {
  const now = Date.now();
  const timeline = progress.timeline ? [...progress.timeline] : [];
  const steps = [...progress.steps];
  const next: TurnProgress = { ...progress, steps, timeline, last_event_at: now };

  /** Закрыть открытые стадии. Возвращает новый массив: объекты из прошлого
   *  состояния не мутируются (важно для memo и DevTools). */
  const closeOpenStage = (targetStage?: TimelineStage["stage"]): TimelineEntry[] =>
    timeline.map((entry) => {
      if (
        entry.kind !== "stage" ||
        entry.finished_at !== undefined ||
        (targetStage !== undefined && entry.stage !== targetStage)
      ) {
        return entry;
      }
      return {
        ...entry,
        finished_at: now,
        elapsed_ms: event.elapsed_ms ?? Math.max(1, now - entry.started_at),
        status: event.status === "failed" ? "failed" : "finished",
        error: event.error ?? entry.error,
        planned_steps: event.planned_steps ?? entry.planned_steps,
      };
    });

  /** Обновить реальную фазу у открытой стадии, не создавая новую. */
  const setSubstage = (stageName: TimelineStage["stage"]): TimelineEntry[] =>
    timeline.map((entry) =>
      entry.kind === "stage" && entry.stage === stageName && entry.finished_at === undefined
        ? { ...entry, substage: event.substage }
        : entry
    );

  if (event.stage === "outline") {
    next.outlining = event.status === "started";
  } else if (event.stage === "tile" && event.tile_id !== undefined) {
    const tiles = [...(next.tiles ?? [])];
    const index = tiles.findIndex((tile) => tile.tile_id === event.tile_id);
    const prev = index >= 0 ? tiles[index] : undefined;
    const tileStatus: TileState["status"] =
      event.status === "fixing" || event.status === "substage"
        ? (prev?.status ?? "started")
        : event.status;
    const tile: TileState = {
      ...prev,
      tile_id: event.tile_id,
      title: event.purpose ?? prev?.title ?? `#${event.tile_id + 1}`,
      status: tileStatus,
      error: event.error ?? prev?.error,
      elapsed_ms: event.elapsed_ms ?? prev?.elapsed_ms,
    };
    if (index >= 0) tiles[index] = tile;
    else tiles.push(tile);
    next.tiles = tiles;
  } else if (event.stage === "toolgen") {
    next.toolgen = event.status === "started";
    next.planning = false;
  } else if (event.stage === "planning") {
    next.toolgen = false;
    if (event.status === "started") {
      next.planning = true;
      next.stage = undefined;
      next.timeline = [...timeline, openStage("planning", now, "progress.stage.planning")];
    } else if (event.status === "substage") {
      next.timeline = setSubstage("planning");
    } else {
      next.planning = false;
      if (event.total_steps !== undefined) next.total_steps = event.total_steps;
      next.timeline = closeOpenStage("planning");
    }
  } else if (event.stage === "replanning") {
    next.toolgen = false;
    if (event.status === "started") {
      next.planning = false;
      next.stage = "replanning";
      next.timeline = [...timeline, openStage("replanning", now, "progress.stage.replanning")];
    } else if (event.status === "substage") {
      next.timeline = setSubstage("replanning");
    } else {
      next.stage = undefined;
      if (event.total_steps !== undefined) next.total_steps = event.total_steps;
      next.timeline = closeOpenStage("replanning");
    }
  } else if (event.stage === "step" && event.step_id !== undefined) {
    next.timeline = closeOpenStage();
    const index = steps.findIndex((step) => step.step_id === event.step_id);
    const prev = index >= 0 ? steps[index] : undefined;
    const stepStatus: StepState["status"] =
      event.status === "substage" ? prev?.status ?? "started" : event.status;
    const step: StepState = {
      ...prev,
      step_id: event.step_id,
      tool: event.tool ?? prev?.tool,
      purpose: event.purpose ?? prev?.purpose,
      status: stepStatus,
      attempt: event.attempt ?? prev?.attempt,
      error: event.error ?? prev?.error,
      elapsed_ms: event.elapsed_ms ?? prev?.elapsed_ms,
      fixed: event.status === "finished" && prev?.status === "fixing" ? true : prev?.fixed,
      tile_id: event.tile_id ?? prev?.tile_id,
    };
    if (index >= 0) steps[index] = step;
    else steps.push(step);

    const tIndex = next.timeline.findIndex(
      (entry) => entry.kind === "step" && entry.step.step_id === event.step_id
    );
    const entry: TimelineStep = { kind: "step", id: `step-${event.step_id}`, step };
    next.timeline =
      tIndex >= 0
        ? next.timeline.map((item, itemIndex) => (itemIndex === tIndex ? entry : item))
        : [...next.timeline, entry];
    next.planning = false;
    next.toolgen = false;
  } else if (event.stage === "insight" || event.stage === "chart" || event.stage === "dashboard") {
    next.toolgen = false;
    if (event.status === "started") {
      next.planning = false;
      next.stage = event.stage;
      next.timeline = [
        ...closeOpenStage(),
        openStage(event.stage, now, `progress.stage.${event.stage}` as MessageKey),
      ];
    } else if (event.status === "finished" || event.status === "failed") {
      next.timeline = closeOpenStage(event.stage);
      next.stage = undefined;
    }
  } else {
    next.planning = false;
    next.toolgen = false;
    next.stage = event.stage;
  }
  return next;
}

/** Применить план дашборда: скелетоны плиток со статусом «в очереди». */
export function applyOutline(progress: TurnProgress, outline: DashboardOutlineEvent["data"]): TurnProgress {
  const known = new Map((progress.tiles ?? []).map((tile) => [tile.tile_id, tile]));
  return {
    ...progress,
    outlining: false,
    last_event_at: Date.now(),
    tiles: outline.tiles.map(
      (tile) => known.get(tile.tile_id) ?? { tile_id: tile.tile_id, title: tile.title, intent: tile.intent, status: "queued" },
    ),
  };
}

/** recovery_response кнопки «Пропустить этот шаг» (см. _apply_recovery). */
export const RECOVERY_SKIP_STEP = JSON.stringify({ action: "skip_step" });
