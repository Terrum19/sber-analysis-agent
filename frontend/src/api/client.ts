/**
 * Клиент бэкенда: создание сессии, диалог (SSE), дашборды.
 * Всё через /api — проксируется на локальный FastAPI (см. next.config.mjs).
 */
import type { ChatStreamEvent } from "@/types/chat";

const API = "/api";

export async function createSession(datasourceId?: string): Promise<{ session_id: string }> {
  const res = await fetch(`${API}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ datasource_id: datasourceId ?? null }),
  });
  return res.json();
}

/** Подписка на SSE-поток диалога; onEvent вызывается на каждое событие. */
export function streamChat(
  sessionId: string,
  question: string,
  onEvent: (event: ChatStreamEvent) => void,
): () => void {
  const controller = new AbortController();

  (async () => {
    const res = await fetch(`${API}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, question }),
      signal: controller.signal,
    });
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
        if (data) onEvent(JSON.parse(data) as ChatStreamEvent);
      }
    }
  })();

  return () => controller.abort();
}

export async function getDashboard(id: string): Promise<Record<string, unknown>> {
  const res = await fetch(`${API}/dashboards/${id}`);
  return res.json();
}
