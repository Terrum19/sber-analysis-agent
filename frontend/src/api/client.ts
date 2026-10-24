/**
 * Клиент бэкенда с поддержкой автономного Демо/Mock-режима.
 * Моки используются ТОЛЬКО если включён флаг NEXT_PUBLIC_USE_MOCKS=true
 * или установлен ключ localStorage.sber_force_mock="true".
 * При ошибках сети или бэкенда выбрасывается ApiError.
 */
import type { ChatStreamEvent } from "@/types/chat";
import type { Datasource, DatasourceDbRequest, SessionResponse } from "@/types/datasource";
import type {
  DashboardConfig,
  DashboardCreatePayload,
  DashboardListItem,
  DashboardUpdatePayload,
} from "@/types/dashboard";
import {
  mockAttachCsv,
  mockAttachDb,
  mockCreateDashboard,
  mockCreateSession,
  mockDeleteDashboard,
  mockFetchDashboard,
  mockFetchDashboards,
  mockListSessions,
  mockListTables,
  mockPatchSession,
  mockRecalculateDashboard,
  mockStreamChat,
  mockUpdateDashboard,
} from "./mockData";

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

export function setMockMode(enabled: boolean): void {
  if (typeof window !== "undefined") {
    localStorage.setItem("sber_force_mock", enabled ? "true" : "false");
    window.location.reload();
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

/**
 * Подписка на SSE-поток диалога; onEvent вызывается на каждое событие,
 * onClose — один раз, когда поток закончился, оборвался или был отменён.
 */
export function streamChat(
  sessionId: string,
  question: string,
  onEvent: (event: ChatStreamEvent) => void,
  onClose?: (error?: string) => void,
  optionsOrRecoveryResponse?: { recoveryResponse?: string } | string,
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
export async function exportArtifact(
  sessionId: string,
  format: "xlsx" | "pdf",
  resultRef?: string,
): Promise<Blob> {
  if (isMockMode()) {
    const content = `Экспорт дашборда (${format.toUpperCase()})\nСессия: ${sessionId}\nДата: ${new Date().toLocaleString("ru-RU")}`;
    return new Blob([content], { type: format === "xlsx" ? "application/vnd.ms-excel" : "application/pdf" });
  }
  try {
    const res = await fetch(`${API}/artifacts/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, format, result_ref: resultRef || null }),
    });
    if (!res.ok) throw await parseApiError(res);
    return await res.blob();
  } catch (err) {
    throw toApiError(err);
  }
}
