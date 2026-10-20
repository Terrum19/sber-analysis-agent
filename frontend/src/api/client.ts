/**
 * Клиент бэкенда: создание сессии, источники данных, диалог (SSE), дашборды.
 * Всё через /api — проксируется на локальный FastAPI (см. next.config.mjs).
 */
import type { ChatStreamEvent } from "@/types/chat";
import type { Datasource, DatasourceDbRequest } from "@/types/datasource";

const API = "/api";

/** Ответ на уточнение «пропустить шаг» (см. orchestrator._apply_recovery). */
export const RECOVERY_SKIP_STEP = JSON.stringify({ action: "skip_step" });

export async function createSession(datasourceId?: string): Promise<{ session_id: string }> {
  const res = await fetch(`${API}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ datasource_id: datasourceId ?? null }),
  });
  return res.json();
}

/**
 * Подписка на SSE-поток диалога; onEvent вызывается на каждое событие,
 * onClose — один раз, когда поток закончился, оборвался или был отменён.
 * recoveryResponse — ответ на clarification_needed: свободный текст
 * уточнения или JSON с действием (RECOVERY_SKIP_STEP).
 */
export function streamChat(
  sessionId: string,
  question: string,
  onEvent: (event: ChatStreamEvent) => void,
  onClose?: (error?: string) => void,
  recoveryResponse?: string,
): () => void {
  const controller = new AbortController();

  (async () => {
    const res = await fetch(`${API}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, question, recovery_response: recoveryResponse ?? null }),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(await errorDetail(res));
    const reader = res.body?.getReader();
    const decoder = new TextDecoder();
    if (!reader) return;
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
  })().then(
    () => onClose?.(),
    (error: unknown) => {
      if (controller.signal.aborted) onClose?.();
      else onClose?.(error instanceof Error ? error.message : String(error));
    },
  );

  return () => controller.abort();
}

/** Загрузить один или несколько сырых CSV (multipart-поле files). */
export async function attachCsv(files: File[]): Promise<Datasource> {
  const body = new FormData();
  for (const file of files) body.append("files", file);
  const res = await fetch(`${API}/datasources/csv`, { method: "POST", body });
  if (!res.ok) throw new Error(await errorDetail(res));
  return res.json();
}

/** Подключить PostgreSQL / ClickHouse по DSN (только чтение). */
export async function attachDb(payload: DatasourceDbRequest): Promise<Datasource> {
  const res = await fetch(`${API}/datasources/db`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: payload.kind, dsn: payload.dsn, schema: payload.schema || null }),
  });
  if (!res.ok) throw new Error(await errorDetail(res));
  return res.json();
}

async function errorDetail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
    if (body?.detail) return JSON.stringify(body.detail);
  } catch {
    // тело не JSON — достаточно HTTP-статуса
  }
  return `HTTP ${res.status}`;
}

export async function getDashboard(id: string): Promise<Record<string, unknown>> {
  const res = await fetch(`${API}/dashboards/${id}`);
  return res.json();
}
