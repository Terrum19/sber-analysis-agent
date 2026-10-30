/**
 * Типы стрима чата (синхронизировано с backend/app/api/schemas/chat.py).
 * Фронт получает события: text | chart | dashboard | error | done,
 * в режиме дашборда (#224) ещё dashboard_outline и dashboard_fallback,
 * table — агент сам взял таблицу под вопрос.
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

/** Строка плана в событии planning finished (#239): шаг до выполнения. */
export interface PlanStep {
  step_id: number;
  tool?: string | null;
  purpose?: string;
  /** Откуда шаг берёт данные: $source, $prev, $step_N или имя таблицы. */
  input: string;
  arguments?: Record<string, unknown>;
}

/** Колонка результата шага с типом DuckDB. */
export interface ResultColumn {
  name: string;
  type: string;
}

/** ProgressPayload бэкенда; пустые поля не приходят. */
export interface ProgressPayload {
  stage: "planning" | "step" | "insight" | "chart" | "dashboard" | "outline" | "tile";
  status: "started" | "finished" | "failed" | "fixing";
  step_id?: number;
  tool?: string;
  purpose?: string;
  total_steps?: number;
  elapsed_ms?: number;
  attempt?: number;
  error?: string;
  /** Плитка дашборда, к которой относится шаг или статус. */
  tile_id?: number;
  /** planning finished: весь план; после перепланирования приходит снова. */
  steps?: PlanStep[];
  /** Детали шага (#239): input и arguments — на любом статусе, остальное — на finished. */
  input?: string;
  arguments?: Record<string, unknown>;
  input_table?: string;
  input_step_id?: number;
  result_table?: string;
  rows?: number;
  result_columns?: ResultColumn[];
  /** Первые строки результата; остальное — GET /sessions/{id}/results/{table}. */
  preview?: Record<string, unknown>[];
  code?: string;
  code_language?: "sql" | "python";
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

/** Неудачная попытка шага: с какими аргументами и почему упала. */
export interface StepAttempt {
  attempt: number;
  error?: string;
  arguments?: Record<string, unknown>;
}

/**
 * Шаг плана в прогрессе хода (по событиям progress: planning и step).
 * Детали (#239) лежат здесь же: сообщение с прогрессом сохраняется в истории,
 * поэтому шаг раскрывается и после перезагрузки. Старые сообщения их не имеют.
 */
export interface StepState {
  step_id: number;
  tool?: string;
  purpose?: string;
  /** queued — шаг из плана, ещё не запущен. */
  status: "queued" | "started" | "fixing" | "finished" | "failed";
  attempt?: number;
  error?: string;
  elapsed_ms?: number;
  /** Шаг падал, но исправился. */
  fixed?: boolean;
  tile_id?: number;
  input?: string;
  arguments?: Record<string, unknown>;
  input_table?: string;
  input_step_id?: number;
  result_table?: string;
  rows?: number;
  result_columns?: ResultColumn[];
  preview?: Record<string, unknown>[];
  code?: string;
  code_language?: "sql" | "python";
  /** Попытки исправления по событиям fixing, по порядку. */
  attempts?: StepAttempt[];
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

/** Прогресс одного хода, собранный из событий progress. */
export interface TurnProgress {
  planning: boolean;
  total_steps?: number;
  steps: StepState[];
  /** Текущий этап после шагов: insight | chart | dashboard. */
  stage?: ProgressPayload["stage"];
  started_at: number;
  finished_at?: number;
  /** Последнее событие хода: по нему видно, что агент затих. */
  last_event_at?: number;
  /** Режим дашборда: идёт план плиток (stage outline). */
  outlining?: boolean;
  /** Плитки из dashboard_outline; есть только в режиме дашборда. */
  tiles?: TileState[];
}

/** Применить событие progress к прогрессу хода. */
export function applyProgress(progress: TurnProgress, event: ProgressPayload): TurnProgress {
  const next: TurnProgress = { ...progress, steps: [...progress.steps], last_event_at: Date.now() };
  if (event.stage === "outline") {
    next.outlining = event.status === "started";
  } else if (event.stage === "tile" && event.tile_id !== undefined) {
    const tiles = [...(next.tiles ?? [])];
    const index = tiles.findIndex((tile) => tile.tile_id === event.tile_id);
    const prev = index >= 0 ? tiles[index] : undefined;
    const tile: TileState = {
      ...prev,
      tile_id: event.tile_id,
      title: event.purpose ?? prev?.title ?? `#${event.tile_id + 1}`,
      status: event.status === "fixing" ? "started" : event.status,
      error: event.error ?? prev?.error,
      elapsed_ms: event.elapsed_ms ?? prev?.elapsed_ms,
    };
    if (index >= 0) tiles[index] = tile;
    else tiles.push(tile);
    next.tiles = tiles;
  } else if (event.stage === "planning") {
    next.planning = event.status === "started";
    if (event.total_steps !== undefined) next.total_steps = event.total_steps;
    if (event.status === "finished" && event.steps) next.steps = applyPlan(next.steps, event.steps, event.tile_id);
  } else if (event.stage === "step" && event.step_id !== undefined) {
    const index = next.steps.findIndex((step) => step.step_id === event.step_id);
    const prev = index >= 0 ? next.steps[index] : undefined;
    const attempts =
      event.status === "fixing"
        ? [
            ...(prev?.attempts ?? []),
            { attempt: event.attempt ?? (prev?.attempts?.length ?? 0) + 1, error: event.error, arguments: event.arguments },
          ]
        : prev?.attempts;
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
      tile_id: event.tile_id ?? prev?.tile_id,
      input: event.input ?? prev?.input,
      arguments: event.arguments ?? prev?.arguments,
      input_table: event.input_table ?? prev?.input_table,
      input_step_id: event.input_step_id ?? prev?.input_step_id,
      result_table: event.result_table ?? prev?.result_table,
      rows: event.rows ?? prev?.rows,
      result_columns: event.result_columns ?? prev?.result_columns,
      preview: event.preview ?? prev?.preview,
      code: event.code ?? prev?.code,
      code_language: event.code_language ?? prev?.code_language,
      attempts,
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

/**
 * План хода (или плитки tileId) целиком: шаги, которых ещё не было, встают
 * «в очередь». После перепланирования выпавшие из плана шаги в очереди убираем,
 * а начатые оставляем — они уже что-то сделали с данными.
 */
function applyPlan(steps: StepState[], plan: PlanStep[], tileId: number | undefined): StepState[] {
  const planned = new Set(plan.map((step) => step.step_id));
  const known = new Map(steps.map((step) => [step.step_id, step]));
  const others = steps.filter((step) => step.tile_id !== tileId);
  const ran = steps.filter((step) => step.tile_id === tileId && step.status !== "queued" && !planned.has(step.step_id));
  const rows = plan.map((item): StepState => {
    const prev = known.get(item.step_id);
    if (prev && prev.status !== "queued") return prev;
    return {
      step_id: item.step_id,
      tool: item.tool ?? undefined,
      purpose: item.purpose || undefined,
      input: item.input,
      arguments: item.arguments,
      status: "queued",
      tile_id: tileId,
    };
  });
  return [...others, ...ran, ...rows];
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
