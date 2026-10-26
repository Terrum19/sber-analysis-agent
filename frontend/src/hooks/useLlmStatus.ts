import { useEffect, useState } from "react";
import { getLlmStatus } from "@/api/client";
import type { LlmStatus } from "@/types/llm";

// Пока модель не готова — опрашиваем часто, чтобы прогресс скачивания шёл плавно;
// готова — редко, чтобы заметить, что Ollama пропала.
const NOT_READY_POLL_MS = 3000;
const READY_POLL_MS = 30000;

/** Состояние LLM с бэкенда; null — ещё не узнали или бэкенд не ответил. */
export function useLlmStatus(): LlmStatus | null {
  const [status, setStatus] = useState<LlmStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function poll() {
      let next: LlmStatus | null = null;
      try {
        next = await getLlmStatus();
      } catch {
        // Бэкенд ещё стартует или недоступен — индикатор не показываем, спросим снова.
      }
      if (cancelled) return;
      setStatus(next);
      timer = setTimeout(poll, next?.state === "ok" ? READY_POLL_MS : NOT_READY_POLL_MS);
    }

    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  return status;
}
