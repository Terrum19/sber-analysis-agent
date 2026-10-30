/**
 * Клиент бэкенда с поддержкой автономного Демо/Mock-режима.
 * Моки используются ТОЛЬКО если включён флаг NEXT_PUBLIC_USE_MOCKS=true
 * или установлен ключ localStorage.sber_force_mock="true".
 * При ошибках сети или бэкенда выбрасывается ApiError.
 */
import type { ChatStreamEvent, DashboardIdea } from "@/types/chat";
import type { LlmStatus } from "@/types/llm";
import type { Datasource, DatasourceDbRequest, SessionResponse, TableBrief } from "@/types/datasource";
import type {
  DashboardConfig,
  DashboardCreatePayload,
  DashboardListItem,
  DashboardUpdatePayload,
} from "@/types/dashboard";
import {
  mockAttachCsv,
  mockAttachDb,
  mockBrief,
  mockCreateDashboard,
  mockCreateSession,
  mockDashboardIdeas,
  mockDeleteDashboard,
  mockFetchDashboard,
  mockFetchDashboards,
  mockListSessions,
  mockListTables,
  mockPatchSession,
  mockRenameSession,
  mockRecalculateDashboard,
  mockStreamChat,
  mockUpdateDashboard,
} from "./mockData";
import { filenameFromDisposition } from "@/lib/download";

const API = "/api";

export class ApiError extends Error {
  public status: number;
  public detail: string;

  constructor(status: number, detail: string) {
    super(`API Error ${status}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function parseApiError(res: Response): Promise<ApiError> {
  let detail = `HTTP ${res.status}`;
  try {
    const text = await res.text();
    if (text) {
      try {
        const body = JSON.parse(text);
        if (typeof body?.detail === "string") {
          detail = body.detail;
        } else if (body?.detail) {
          detail = JSON.stringify(body.detail);
        } else if (typeof body?.message === "string") {
          detail = body.message;
        } else {
          detail = text;
        }
      } catch {
        detail = text;
      }
    }
  } catch {
    // тело не удалось прочесть
  }
  return new ApiError(res.status, detail);
}

function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;
  const message = err instanceof Error ? err.message : String(err);
  return new ApiError(0, message);
}

export function isMockMode(): boolean {
  if (process.env.NEXT_PUBLIC_USE_MOCKS === "true") return true;
  if (typeof window !== "undefined") {
    return localStorage.getItem("sber_force_mock") === "true";
  }
  return false;
}

/** GET /api/llm/status: готова ли модель (скачивание и подготовку бэкенд делает сам). */
export async function getLlmStatus(): Promise<LlmStatus> {
  if (isMockMode()) {
    return { state: "ok", model: "mock" };
  }
  try {
    const res = await fetch(`${API}/llm/status`);
    if (!res.ok) throw await parseApiError(res);
    return (await res.json()) as LlmStatus;
  } catch (err) {
    throw toApiError(err);
  }
}

export async function createSession(
  table: string,
  datasourceId?: string | null,
  title?: string | null,
): Promise<SessionResponse> {
  if (isMockMode()) {
    return mockCreateSession(table, datasourceId ?? undefined, title ?? undefined);
  }
  try {
    const res = await fetch(`${API}/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        table,
        datasource_id: datasourceId ?? null,
        title: title ?? null,
      }),
    });
    if (!res.ok) throw await parseApiError(res);
    return await res.json();
  } catch (err) {
    const apiErr = toApiError(err);
    if (apiErr.status === 404) {
      apiErr.detail = "Таблица не найдена";
    }
    throw apiErr;
  }
}

export async function deleteSessionApi(sessionId: string): Promise<{ ok: boolean }> {
  if (isMockMode()) {
    return { ok: true };
  }
  try {
    const res = await fetch(`${API}/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    });
    if (!res.ok) throw await parseApiError(res);
    return { ok: true };
  } catch (err) {
    throw toApiError(err);
  }
}

export async function patchSession(
  sessionId: string,
  table: string,
): Promise<SessionResponse> {
  if (isMockMode()) {
    return mockPatchSession(sessionId, table);
  }
  try {
    const res = await fetch(`${API}/sessions/${encodeURIComponent(sessionId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ table }),
    });
    if (!res.ok) throw await parseApiError(res);
    return await res.json();
  } catch (err) {
    const apiErr = toApiError(err);
    if (apiErr.status === 404) {
      apiErr.detail = "Таблица не найдена";
    }
    throw apiErr;
  }
}

export async function getSession(sessionId: string): Promise<SessionResponse> {
  if (isMockMode()) {
    return { session_id: sessionId, table: "mock_table" };
  }
  try {
    const res = await fetch(`${API}/sessions/${encodeURIComponent(sessionId)}`);
    if (!res.ok) throw await parseApiError(res);
    return await res.json();
  } catch (err) {
    const apiErr = toApiError(err);
    if (apiErr.status === 404) {
      apiErr.detail = "Сессия не найдена";
    }
    throw apiErr;
  }
}

/** PATCH /api/sessions/{id} {table}: сменить таблицу сессии (404 — «Таблица не найдена»). */
export const setSessionTable = patchSession;

/** PATCH /api/sessions/{id} {title}: переименовать сессию на бэкенде (404 — «Сессия не найдена»). */
export async function renameSessionApi(sessionId: string, title: string): Promise<SessionResponse> {
  if (isMockMode()) {
    return mockRenameSession(sessionId, title);
  }
  try {
    const res = await fetch(`${API}/sessions/${encodeURIComponent(sessionId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    if (!res.ok) throw await parseApiError(res);
    return await res.json();
  } catch (err) {
    const apiErr = toApiError(err);
    if (apiErr.status === 404) {
      apiErr.detail = "Сессия не найдена";
    }
    throw apiErr;
  }
}

/** Страница сессий (до SESSIONS_PAGE штук), недавно изменённые первыми. */
export const SESSIONS_PAGE = 30;

export async function listSessions(offset = 0): Promise<SessionResponse[]> {
  if (isMockMode()) {
    return mockListSessions(offset);
  }
  try {
    const res = await fetch(`${API}/sessions?offset=${offset}`);
    if (!res.ok) throw await parseApiError(res);
    return await res.json();
  } catch (err) {
    throw toApiError(err);
  }
}

/** Удаляет источник на бэкенде: его таблицы, паспорта и файлы (CSV) или подключение (БД). */
export async function deleteDatasourceApi(datasourceId: string): Promise<{ ok: boolean }> {
  if (isMockMode()) {
    return { ok: true };
  }
  try {
    const res = await fetch(`${API}/datasources/${encodeURIComponent(datasourceId)}`, {
      method: "DELETE",
    });
    if (!res.ok) throw await parseApiError(res);
    return { ok: true };
  } catch (err) {
    throw toApiError(err);
  }
}

/** Таблицы пользователя по алфавиту (CSV и PostgreSQL). */
export async function listTables(): Promise<string[]> {
  if (isMockMode()) {
    return mockListTables();
  }
  try {
    const res = await fetch(`${API}/datasources/tables`);
    if (!res.ok) throw await parseApiError(res);
    const body: Array<string | { table: string }> = await res.json();
    return body.map((item) => (typeof item === "string" ? item : item.table));
  } catch (err) {
    throw toApiError(err);
  }
}

/** Сводка паспорта таблицы: описание и название от LLM появляются в ней после подключения. */
export async function getTableBrief(table: string): Promise<TableBrief> {
  if (isMockMode()) {
    return mockBrief(table);
  }
  try {
    const res = await fetch(`${API}/datasources/tables/${encodeURIComponent(table)}`);
    if (!res.ok) throw await parseApiError(res);
    return await res.json();
  } catch (err) {
    throw toApiError(err);
  }
}

/** PATCH /api/datasources/tables/{table}: название таблицы для пользователя; имя в SQL прежнее. */
export async function renameTableApi(table: string, displayName: string): Promise<TableBrief> {
  if (isMockMode()) {
    return { ...mockBrief(table), display_name: displayName };
  }
  try {
    const res = await fetch(`${API}/datasources/tables/${encodeURIComponent(table)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ display_name: displayName }),
    });
    if (!res.ok) throw await parseApiError(res);
    return await res.json();
  } catch (err) {
    throw toApiError(err);
  }
}

/**
 * POST /api/sessions/{id}/dashboard-ideas: 4 варианта дашборда по таблице сессии (#224).
 * exclude — названия прежних вариантов: тогда приходят новые, их не повторяющие.
 * 404 — нет сессии, 409 — нет таблицы, 502 — модель не ответила.
 */
export async function fetchDashboardIdeas(sessionId: string, exclude: string[] = []): Promise<DashboardIdea[]> {
  if (isMockMode()) {
    return mockDashboardIdeas(exclude);
  }
  try {
    const res = await fetch(`${API}/sessions/${encodeURIComponent(sessionId)}/dashboard-ideas`, {
      method: "POST",
      ...(exclude.length > 0
        ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify({ exclude }) }
        : {}),
    });
    if (!res.ok) throw await parseApiError(res);
    const body: { ideas: DashboardIdea[] } = await res.json();
    return body.ideas;
  } catch (err) {
    throw toApiError(err);
  }
}

/** Запрос хода в режиме дашборда; idea — выбранный вариант или null. */
export interface DashboardRequest {
  idea: DashboardIdea | null;
}

/**
 * Подписка на SSE-поток диалога; onEvent вызывается на каждое событие,
 * onClose — один раз, когда поток закончился, оборвался или был отменён.
 * replyTo — turn_id из события done: вопрос продолжает конкретный прошлый ход (#166).
 * dashboard — ход строит дашборд (mode: "dashboard", #224).
 */
export function streamChat(
  sessionId: string,
  question: string,
  onEvent: (event: ChatStreamEvent) => void,
  onClose?: (error?: string) => void,
  optionsOrRecoveryResponse?:
    | { recoveryResponse?: string; replyTo?: string | null; dashboard?: DashboardRequest }
    | string,
): () => void {
  const options =
    typeof optionsOrRecoveryResponse === "string"
      ? { recoveryResponse: optionsOrRecoveryResponse }
      : optionsOrRecoveryResponse;

  if (isMockMode()) {
    return mockStreamChat(sessionId, question, onEvent, onClose, options);
  }

  const controller = new AbortController();

  (async () => {
    try {
      const res = await fetch(`${API}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          question,
          recovery_response: options?.recoveryResponse ?? null,
          reply_to: options?.replyTo ?? null,
          mode: options?.dashboard ? "dashboard" : "chat",
          idea: options?.dashboard?.idea ?? null,
        }),
        signal: controller.signal,
      });

      if (!res.ok) {
        throw await parseApiError(res);
      }

      const reader = res.body?.getReader();
      const decoder = new TextDecoder();
      if (!reader) {
        throw new ApiError(res.status, "Empty response body");
      }
      let buffer = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          // SSE-комментарий (": ping" бэкенда) держит соединение, событием не является
          if (line.startsWith(":")) continue;
          const data = line.replace(/^data: /, "").trim();
          if (!data) continue;
          let event: ChatStreamEvent;
          try {
            event = JSON.parse(data) as ChatStreamEvent;
          } catch {
            continue;
          }
          onEvent(event);
        }
      }
      onClose?.();
    } catch (err: any) {
      if (controller.signal.aborted) {
        onClose?.();
        return;
      }
      const apiErr = toApiError(err);
      console.error("[SberClient] Ошибка обращения к бэкенду:", apiErr);
      onClose?.(apiErr.detail || apiErr.message);
    }
  })();

  return () => controller.abort();
}

/**
 * Загрузить один или несколько сырых CSV (multipart-поле files).
 * description — необязательный документ-описание данных в том же multipart.
 */
export async function attachCsv(files: File[], description?: File | null): Promise<Datasource> {
  if (isMockMode()) {
    return mockAttachCsv(files);
  }
  try {
    const body = new FormData();
    for (const file of files) body.append("files", file);
    if (description) body.append("description", description);
    const res = await fetch(`${API}/datasources/csv`, { method: "POST", body });
    if (!res.ok) throw await parseApiError(res);
    return await res.json();
  } catch (err) {
    throw toApiError(err);
  }
}

/** Подключить PostgreSQL / ClickHouse по DSN (только чтение). */
export async function attachDb(payload: DatasourceDbRequest): Promise<Datasource> {
  if (isMockMode()) {
    return mockAttachDb(payload.kind, payload.dsn);
  }
  try {
    const res = await fetch(`${API}/datasources/db`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind: payload.kind,
        dsn: payload.dsn,
        schema: payload.schema || null,
        description: payload.description || null,
      }),
    });
    if (!res.ok) throw await parseApiError(res);
    return await res.json();
  } catch (err) {
    throw toApiError(err);
  }
}

/* ========================================================================= */
/*                          API ДАШБОРДОВ                                     */
/* ========================================================================= */

export function isNotFound(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404;
}

/** Получить список дашбордов (всех или конкретной сессии). */
export async function fetchDashboards(
  sessionId?: string,
  signal?: AbortSignal,
): Promise<DashboardListItem[]> {
  if (isMockMode()) {
    return mockFetchDashboards(sessionId);
  }
  try {
    const url = sessionId
      ? `${API}/dashboards?session_id=${encodeURIComponent(sessionId)}`
      : `${API}/dashboards`;
    const res = await fetch(url, { signal });
    if (!res.ok) throw await parseApiError(res);
    return await res.json();
  } catch (err) {
    throw toApiError(err);
  }
}

/** Получить полный конфиг дашборда по ID. */
export async function fetchDashboard(id: string): Promise<DashboardConfig> {
  if (isMockMode()) {
    return mockFetchDashboard(id);
  }
  try {
    const res = await fetch(`${API}/dashboards/${id}`);
    if (!res.ok) throw await parseApiError(res);
    return await res.json();
  } catch (err) {
    throw toApiError(err);
  }
}

/** Создать дашборд. */
export async function createDashboard(payload: DashboardCreatePayload): Promise<DashboardConfig> {
  if (isMockMode()) {
    return mockCreateDashboard(payload);
  }
  try {
    const res = await fetch(`${API}/dashboards`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw await parseApiError(res);
    return await res.json();
  } catch (err) {
    throw toApiError(err);
  }
}

/** Обновить заголовок, плитки или раскладку дашборда. */
export async function updateDashboard(
  id: string,
  payload: DashboardUpdatePayload,
): Promise<DashboardConfig> {
  if (isMockMode()) {
    return mockUpdateDashboard(id, payload);
  }
  try {
    const res = await fetch(`${API}/dashboards/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw await parseApiError(res);
    return await res.json();
  } catch (err) {
    throw toApiError(err);
  }
}

/** Удалить дашборд по ID. */
export async function deleteDashboard(id: string): Promise<{ ok: boolean }> {
  if (isMockMode()) {
    return mockDeleteDashboard(id);
  }
  try {
    const res = await fetch(`${API}/dashboards/${id}`, {
      method: "DELETE",
    });
    if (!res.ok) throw await parseApiError(res);
    if (res.status === 204 || res.headers.get("content-length") === "0") {
      return { ok: true };
    }
    const text = await res.text();
    if (!text) return { ok: true };
    try {
      return JSON.parse(text);
    } catch {
      return { ok: true };
    }
  } catch (err) {
    throw toApiError(err);
  }
}

/** Пересчитать снимки данных плиток дашборда. */
export async function recalculateDashboard(id: string): Promise<DashboardConfig> {
  if (isMockMode()) {
    return mockRecalculateDashboard(id);
  }
  try {
    const res = await fetch(`${API}/dashboards/${id}/recalculate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    if (!res.ok) throw await parseApiError(res);
    return await res.json();
  } catch (err) {
    throw toApiError(err);
  }
}

/** Выгрузка артефактов (Excel / PDF). */
export interface ExportPayload {
  session_id: string;
  format: "xlsx" | "pdf";
  /** Ссылка на temp_result_vN в DuckDB (обязательна для Excel). */
  result_ref?: string | null;
  /** Дашборд-источник: владение результатом проверяется по его плиткам. */
  dashboard_id?: string;
  /** Вопрос пользователя — для PDF-отчёта (E-01). */
  question?: string;
  /** Заголовок инсайта — для PDF-отчёта (E-01). */
  headline?: string;
  /** Детали инсайта — для PDF-отчёта (E-01). */
  details?: string[];
  /** Заголовок отчёта / дашборда из интерфейса. */
  title?: string;
  /** Название сессии для шапки отчёта дашборда. */
  session_title?: string;
  /** Название базы данных для шапки отчёта дашборда. */
  database_name?: string;
  /** Название источника / базы данных / файла для шапки отчёта дашборда. */
  source_name?: string;
  /** Vega-Lite спека графика — для картинки в PDF (E-01). */
  spec?: Record<string, unknown>;
}

export interface ExportResult {
  blob: Blob;
  /** Имя из Content-Disposition либо запасное. */
  filename: string;
}

export async function exportArtifact(
  payload: ExportPayload,
  fallbackName?: string,
): Promise<ExportResult> {
  const { session_id, format, result_ref, dashboard_id, ...insight } = payload;
  const fallback = fallbackName ?? `agent_export.${format}`;
  if (isMockMode()) {
    const content = `Экспорт отчёта (${format.toUpperCase()})\nСессия: ${session_id}\nДата: ${new Date().toLocaleString("ru-RU")}`;
    return {
      blob: new Blob([content], {
        type: format === "xlsx" ? "application/vnd.ms-excel" : "application/pdf",
      }),
      filename: fallback,
    };
  }
  try {
    const res = await fetch(`${API}/artifacts/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id,
        format,
        result_ref: result_ref || null,
        dashboard_id: dashboard_id || null,
        ...insight,
      }),
    });
    if (!res.ok) throw await parseApiError(res);
    const blob = await res.blob();
    return {
      blob,
      filename: filenameFromDisposition(res.headers.get("Content-Disposition"), fallback),
    };
  } catch (err) {
    throw toApiError(err);
  }
}
